# argus/bot/nvd/client.py
import os
import time
import requests
from requests.adapters import HTTPAdapter
from dotenv import load_dotenv
from typing import List, Dict, Any
import logging
import random
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

from nvd.matching import get_cves_for_product, get_cves_for_query

# Load environment variables
load_dotenv()

# Configuration
NVD_API_KEY = os.getenv("NVD_API_KEY")
BASE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
TIMEOUT = 30

_RETRYABLE_STATUSES = {429, 503}
_MAX_RETRIES = 5
_BACKOFF_BASE = 6
_BACKOFF_CAP  = 120

# Initialize logging
logger = logging.getLogger(__name__)

_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=4, pool_maxsize=10)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)
_session.headers.update({"User-Agent": "ARGUS-Vulnerability-Scanner/1.0"})

def _get_retry_delay(response, attempt: int) -> float:
    """
    Return a safe retry delay for NVD 429/503 responses.

    NVD may return Retry-After as:
    - seconds, for example: "30"
    - HTTP date
    - invalid/zero value

    A zero or invalid value must never cause immediate retry.
    """
    base_delay = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP)
    retry_after = response.headers.get("Retry-After", "").strip()

    if retry_after:
        # Retry-After expressed as seconds.
        if retry_after.isdigit():
            server_delay = int(retry_after)

            # Never retry immediately after 429/503.
            if server_delay > 0:
                return min(server_delay, _BACKOFF_CAP)

        # Retry-After expressed as an HTTP date.
        else:
            try:
                retry_time = parsedate_to_datetime(retry_after)
                if retry_time.tzinfo is None:
                    retry_time = retry_time.replace(tzinfo=timezone.utc)

                seconds_until_retry = (
                    retry_time - datetime.now(timezone.utc)
                ).total_seconds()

                if seconds_until_retry > 0:
                    return min(seconds_until_retry, _BACKOFF_CAP)

            except (TypeError, ValueError, IndexError):
                pass

    # Add small jitter so parallel requests do not retry together.
    jitter = random.uniform(0.5, 2.0)

    # Minimum delay is 6 seconds even if NVD says Retry-After: 0.
    return max(6.0, min(base_delay + jitter, _BACKOFF_CAP))

def search_cve_page(keyword: str, start_index: int, results_per_page: int = 100) -> Dict:
    """Raw NVD API call with exponential backoff on 429/503.

    Raises requests.HTTPError only after all retries are exhausted.
    """
    headers = {}
    if NVD_API_KEY:
        headers["apiKey"] = NVD_API_KEY
    params = {
        "keywordSearch": keyword,
        "resultsPerPage": results_per_page,
        "startIndex": start_index,
    }

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = _session.get(
                BASE_URL,
                headers=headers,
                params=params,
                timeout=TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            wait = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP)
            logger.warning(
                "[NVD] Read timed out for keyword='%s' (attempt %d/%d). Retrying in %ds.",
                keyword, attempt + 1, _MAX_RETRIES, wait,
            )
            time.sleep(wait)
            continue

        if response.status_code not in _RETRYABLE_STATUSES:
            # 2xx → return; any other 4xx/5xx → raise immediately (no retry)
            response.raise_for_status()
            return response.json()

        last_exc = requests.HTTPError(response=response)

        # Honour Retry-After if the server sends it
        wait = _get_retry_delay(response, attempt)

        logger.warning(
            "[NVD] %s for keyword='%s' (attempt %d/%d). Retrying in %.1fs. Retry-After=%r",
            response.status_code, keyword, attempt + 1, _MAX_RETRIES, wait, response.headers.get("Retry-After"),
        )
        time.sleep(wait)

    if last_exc is not None:
        raise last_exc
    
    raise requests.RequestException(
        f"NVD request failed for keyword={keyword!r} without a captured exception"
    )

def get_all_cves(keyword: str) -> List[Dict]:
    """Retrieve all CVEs for a given keyword."""
    all_results = []
    start_index = 0
    page_size = 100

    while True:
        data = search_cve_page(keyword, start_index, page_size)
        vulnerabilities = data.get("vulnerabilities", [])
        all_results.extend(vulnerabilities)

        total_results = data.get("totalResults", 0)
        start_index += page_size

        if start_index >= total_results:
            break

    return all_results

def _extract_cvss(cve_node: Dict) -> tuple:
    """
    Try V3.1 first, fall back to V3.0, then V2.
    Returns (score, severity_string).
    """
    metrics = cve_node.get("metrics", {})

    for key in ("cvssMetricV31", "cvssMetricV30"):
        entries = metrics.get(key, [])
        if entries:
            data = entries[0]["cvssData"]
            return float(data["baseScore"]), data.get("baseSeverity", "NONE")

    entries = metrics.get("cvssMetricV2", [])
    if entries:
        score = float(entries[0]["cvssData"]["baseScore"])
        # V2 has no baseSeverity in all NVD records; derive it
        if score >= 7.0:
            sev = "HIGH"
        elif score >= 4.0:
            sev = "MEDIUM"
        else:
            sev = "LOW"
        return score, sev

    return 0.0, "NONE"

def get_cve_summary(keyword: str, version: str | None = None) -> List[Dict]:
    """
    Retrieve CVEs from NVD and return normalized ARGUS CVE records.

    `keyword` is the product name (e.g. "Fortinet FortiOS" or just
    "FortiOS"). If `version` is given, matching is scoped to that exact
    version via NVD's CPE Dictionary + cpeName -- the same authoritative
    logic Live NVD Search uses, instead of the old vendor+product-only,
    version-blind keywordSearch this used to do. If `version` is omitted,
    `keyword` is parsed the same way Live NVD Search parses a typed query
    (recognizes a literal CVE ID, or a trailing 'Product 1.2.3' version).

    Raises requests.RequestException when NVD cannot be queried successfully.
    The scanner is responsible for recording that failure as a scan error.
    """
    if version:
        vulnerabilities, note, error = get_cves_for_product(keyword, version)
    else:
        vulnerabilities, note, error = get_cves_for_query(keyword)

    if error is not None:
        logger.error("[NVD ERROR] keyword='%s' version='%s' failed: %s", keyword, version, error)
        raise requests.RequestException(error)

    if note:
        logger.info("[NVD] keyword='%s' version='%s': %s", keyword, version, note)

    logger.info("[NVD] keyword='%s' version='%s' -> %d CVEs", keyword, version, len(vulnerabilities),)

    results = []

    for item in vulnerabilities:
        cve = item.get("cve", {})
        cve_id = cve.get("id")

        if not cve_id:
            logger.warning( "[NVD] Skipping malformed vulnerability entry for keyword='%s': missing CVE ID", keyword,)
            continue

        descriptions = cve.get("descriptions", [])

        description = next(
            (
                entry.get("value", "")
                for entry in descriptions
                if entry.get("lang") == "en"
            ),
            descriptions[0].get("value", "") if descriptions else "",
        )

        try:
            cvss, severity = _extract_cvss(cve)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("[NVD] Could not parse CVSS for CVE '%s': %r. Using defaults.", cve_id, exc,)
            cvss, severity = 0.0, "NONE"

        results.append(
            {
                "id": cve_id,
                "description": description,
                "cvss": cvss,
                "severity": severity,
            }
        )

    return results

def check_nvd_api() -> bool:
    """Probe the NVD API with a minimal query; return True if reachable."""
    try:
        search_cve_page("test", 0, results_per_page=1)
        return True
    except requests.RequestException:
        return False