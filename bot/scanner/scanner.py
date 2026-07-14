"""
Centralised scanning logic.

Both /scan (on-demand) and the daily job call scan_asset() / scan_all_assets()
from here — no scanning logic lives in handlers.

Speed improvements:
 - EPSS is fetched in a single batch call per asset (not one HTTP request per CVE).
 - KEV set is loaded once and reused across all CVEs for an asset.
 - scan_all_assets() runs all assets concurrently via asyncio.gather().
 - save_match() reports new-vs-duplicate via its INSERT...RETURNING result,
   removing the separate match_exists() SELECT that used to run before
   every insert (one DB round-trip per CVE instead of two).
"""

import asyncio
import logging
import random
import time
from typing import Dict, List

import requests
from requests import RequestException
from requests.adapters import HTTPAdapter
from database.assets import get_all_assets_full, update_last_scan
from database.cves import save_cve
from database.matches import save_match, save_alert
from nvd.client import get_cve_summary
from risk.scoring import calculate_risk
from alerts.telegram_alert import send_alert
from kev.clients import is_kev, _load_kev_ids

logger = logging.getLogger(__name__)

_epss_session = requests.Session()
_epss_adapter = HTTPAdapter(pool_connections=2, pool_maxsize=4)
_epss_session.mount("https://", _epss_adapter)
_epss_session.headers.update({"User-Agent": "ARGUS-Vulnerability-Scanner/1.0"})

_EPSS_MAX_RETRIES = 3
_EPSS_BACKOFF_BASE = 2  # seconds; doubles each attempt


def _get_epss_batch(cve_ids: List[str]) -> Dict[str, Dict]:
    """
    Fetch EPSS scores for multiple CVEs in a single API call.
    Returns dict keyed by CVE ID → {"epss": float, "percentile": float}.
    Falls back gracefully on any error.
    """
    if not cve_ids:
        return {}

    default = {"epss": 0.0, "percentile": 0.0}
    result = {cid: dict(default) for cid in cve_ids}

    # EPSS API accepts comma-separated CVE IDs (up to ~100 at a time)
    BATCH = 100
    for i in range(0, len(cve_ids), BATCH):
        chunk = cve_ids[i : i + BATCH]
        url = "https://api.first.org/data/v1/epss"
        last_exc: Exception | None = None
        for attempt in range(_EPSS_MAX_RETRIES):
            try:
                resp = _epss_session.get(url, params={"cve": ",".join(chunk)}, timeout=15)
                resp.raise_for_status()
                for row in resp.json().get("data", []):
                    cid = row.get("cve")
                    if cid:
                        result[cid] = {
                            "epss": float(row.get("epss", 0.0)),
                            "percentile": float(row.get("percentile", 0.0)),
                        }
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt < _EPSS_MAX_RETRIES - 1:
                    wait = _EPSS_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "[epss] Batch fetch attempt %d/%d failed for chunk starting %s: %s. Retrying in %.1fs.",
                        attempt + 1, _EPSS_MAX_RETRIES, chunk[0], exc, wait,
                    )
                    time.sleep(wait)
        if last_exc is not None:
            logger.warning("[epss] Batch fetch failed for chunk starting %s after retries: %s", chunk[0], last_exc)

    return result


async def scan_asset(asset: dict) -> dict:
    """
    Scan a single asset dict (as returned by get_asset / get_all_assets_full).

    Returns a result dict:
        {
            "keyword": str,
            "cves": list[dict],
            "new_findings": list[dict],
            "error": str | None,
        }
    """
    vendor = asset["vendor"].strip()
    product = asset["product"].strip()

    if product.lower().startswith(vendor.lower()):
        keyword = product
    else:
        keyword = f"{vendor} {product}"

    if asset.get("search_keyword"):
        keyword = asset["search_keyword"].strip()

    version = (asset.get("version") or "").strip()

    logger.info("[scanner] asset_id=%s keyword='%s' version='%s'", asset['id'], keyword, version)
    result: dict = {
        "keyword": keyword,
        "cves": [],
        "new_findings": [],
        "error": None,
    }

    # ── Fetch CVEs from NVD (blocking I/O in thread pool) ────────────────────
    loop = asyncio.get_running_loop()

    try:
        cves = await loop.run_in_executor(
            None,
            get_cve_summary,
            keyword,
            version,
        )

    except RequestException as exc:
        result["error"] = f"NVD lookup failed: {exc}"
        logger.error(
            "[scanner] NVD lookup failed for keyword='%s'. "
            "Skipping this asset; existing findings will not be removed. Error=%r",
            keyword,
            exc,
        )
        return result

    except Exception as exc:
        result["error"] = f"Unexpected NVD lookup error: {exc}"
        logger.exception(
            "[scanner] Unexpected error fetching CVEs for keyword='%s'",
            keyword,
        )
        return result

    logger.info(
        "[scanner] keyword='%s' -> %d CVEs",
        keyword,
        len(cves),
    )

    if cves is None:
        result["error"] = "NVD lookup failed"
        return result
    
    if not cves:
        return result

    # ── Batch-fetch EPSS for all CVEs in one call ─────────────────────────────
    cve_ids = [c["id"] for c in cves]
    epss_map = await loop.run_in_executor(None, _get_epss_batch, cve_ids)

    # ── Load KEV set once (cached, one fetch per day max) ─────────────────────
    kev_set = await loop.run_in_executor(None, _load_kev_ids)

    # ── Process each CVE ──────────────────────────────────────────────────────
    for cve in cves:
        cid = cve["id"]
        kev = cid in kev_set
        epss_data = epss_map.get(cid, {"epss": 0.0, "percentile": 0.0})
        epss = epss_data["epss"]
        epss_percentile = epss_data["percentile"]
        risk = calculate_risk(cve["cvss"], asset.get("criticality"), kev, epss_percentile)

        await loop.run_in_executor(None, save_cve, cid, cve["cvss"], kev, None, cve["description"], epss, epss_percentile)

        enriched = {
            "id": cid,
            "cvss": cve["cvss"],
            "severity": cve["severity"],
            "risk": risk,
            "kev": kev,
        }
        result["cves"].append(enriched)

        is_new = await loop.run_in_executor(
            None, save_match, asset["id"], cid, risk, cve.get("cvss", 0.0)
        )
        if is_new:
            result["new_findings"].append(enriched)

            try:
                from Ai.analyzer import queue_cve_for_analysis
                await loop.run_in_executor(
                    None, queue_cve_for_analysis, cid, cve.get("description", "")
                )
            except Exception as exc:
                logger.warning(f"[scanner] Failed to queue {cid} for AI analysis: {exc}")

    await loop.run_in_executor(None, update_last_scan, asset["id"])

    # ── Alert on new findings ─────────────────────────────────────────────────
    if result["new_findings"]:
        alert_lines = [
            f"• {f['id']} | CVSS {f['cvss']} | {f['severity']} | Risk {f['risk']}"
            + (" ⚠️ ACTIVE EXPLOIT" if f["kev"] else "")
            for f in result["new_findings"]
        ]
        n = len(result["new_findings"])
        alert_message = (
            f"🚨 ARGUS ALERT\n\n"
            f"Asset: {asset['vendor']} {asset['product']} v{asset.get('version','')}\n"
            f"Type: {asset.get('type', 'Unknown')}\n\n"
            f"{n} New Vulnerabilit{'y' if n == 1 else 'ies'} Found\n\n"
            + "\n".join(alert_lines)
        )
        await send_alert(alert_message)
        try:
            await loop.run_in_executor(None, save_alert, asset["id"], alert_message)
        except Exception:
            pass

    return result


async def scan_all_assets() -> List[dict]:
    """Scan every asset concurrently; individual errors don't abort the rest.

    Concurrency is capped at _NVD_CONCURRENCY to avoid overwhelming the NVD
    API and triggering 429 Too Many Requests errors. With an API key NVD
    allows ~50 req/30 s; without a key the limit is ~5 req/30 s. We default
    to 1 simultaneous asset scan (each asset may itself page through
    multiple NVD calls), which is safe for unauthenticated use; raise
    _NVD_CONCURRENCY once NVD_API_KEY is configured in production, since
    the unauthenticated rate limit is the binding constraint here.
    """
    assets = get_all_assets_full()
    if not assets:
        return []

    _NVD_CONCURRENCY = 1
    semaphore = asyncio.Semaphore(_NVD_CONCURRENCY)

    async def _safe_scan(asset):
        async with semaphore:
            try:
                return await scan_asset(asset)
            except Exception as exc:
                keyword = asset.get("search_keyword") or f"{asset['vendor']} {asset['product']}"
                logger.error(f"[scanner] Unexpected error scanning '{keyword}': {exc}")
                return {
                    "keyword": keyword,
                    "cves": [],
                    "new_findings": [],
                    "error": str(exc),
                }

    return await asyncio.gather(*[_safe_scan(a) for a in assets])
