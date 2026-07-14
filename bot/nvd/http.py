# argus/bot/nvd/http.py
"""
Shared, retry-aware HTTP layer for NVD API calls.

NVD aggressively rate-limits (HTTP 429) and occasionally returns 503; both
are transient and must be retried with backoff, not treated as a hard
failure. nvd/client.py's search_cve_page() has always done this correctly.

When nvd/matching.py was introduced (to unify CVE-matching logic across
Live NVD Search, the asset scanner, and the Telegram /cve command), its
CPE-lookup and keywordSearch/cveId calls used plain requests.get() with NO
retry at all. That went unnoticed for a single interactive Live NVD Search
query (one call, rarely rate-limited), but Scan All Assets makes at least
two NVD calls per asset (a CPE Dictionary lookup plus a cpeName CVE lookup)
across every asset in sequence -- easily enough to hit NVD's unauthenticated
~5-requests-per-30-seconds limit, at which point every 429 was being
treated as a hard scan error instead of being retried. This module exists
so that mistake can only need fixing once, in one place, ever again.
"""
import os
import time
import random
import logging
import requests
from requests.adapters import HTTPAdapter
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

NVD_API_KEY = os.getenv("NVD_API_KEY")

_RETRYABLE_STATUSES = {429, 503}
_MAX_RETRIES = 5
_BACKOFF_BASE = 6
_BACKOFF_CAP = 120

_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=4, pool_maxsize=10)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)
_session.headers.update({"User-Agent": "ARGUS-Vulnerability-Scanner/1.0"})


def _get_retry_delay(response, attempt: int) -> float:
    """Return a safe retry delay for NVD 429/503 responses. Verbatim logic
    from nvd/client.py's version -- NVD may send Retry-After as seconds, an
    HTTP date, or an invalid/zero value; a zero/invalid value must never
    cause an immediate retry."""
    base_delay = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP)
    retry_after = response.headers.get("Retry-After", "").strip()

    if retry_after:
        if retry_after.isdigit():
            server_delay = int(retry_after)
            if server_delay > 0:
                return min(server_delay, _BACKOFF_CAP)
        else:
            try:
                retry_time = parsedate_to_datetime(retry_after)
                if retry_time.tzinfo is None:
                    retry_time = retry_time.replace(tzinfo=timezone.utc)
                seconds_until_retry = (retry_time - datetime.now(timezone.utc)).total_seconds()
                if seconds_until_retry > 0:
                    return min(seconds_until_retry, _BACKOFF_CAP)
            except (TypeError, ValueError, IndexError):
                pass

    jitter = random.uniform(0.5, 2.0)
    return max(6.0, min(base_delay + jitter, _BACKOFF_CAP))


def nvd_headers():
    h = {}
    if NVD_API_KEY:
        h["apiKey"] = NVD_API_KEY
    return h


def get(url: str, timeout=(10, 90)):
    """GET an NVD URL with retry/backoff on 429/503 and on transient
    connection errors.

    Returns the final requests.Response on success OR on a non-retryable
    failure (caller checks .ok/.status_code, same as a plain requests.get
    would). Returns None only if every attempt failed at the connection
    level (never got any HTTP response at all) -- callers must handle that
    case explicitly, since it has no .ok/.status_code to check.
    """
    last_response = None

    for attempt in range(_MAX_RETRIES):
        try:
            response = _session.get(url, headers=nvd_headers(), timeout=timeout)
        except requests.exceptions.RequestException as exc:
            wait = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP)
            logger.warning(
                "[NVD] Request failed for %s (attempt %d/%d): %s. Retrying in %ds.",
                url, attempt + 1, _MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
            continue

        if response.status_code not in _RETRYABLE_STATUSES:
            return response

        last_response = response
        wait = _get_retry_delay(response, attempt)
        logger.warning(
            "[NVD] %s for %s (attempt %d/%d). Retrying in %.1fs. Retry-After=%r",
            response.status_code, url, attempt + 1, _MAX_RETRIES, wait, response.headers.get("Retry-After"),
        )
        time.sleep(wait)

    return last_response
