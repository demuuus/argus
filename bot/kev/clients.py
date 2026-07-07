"""
kev/clients.py — CISA Known Exploited Vulnerabilities (KEV) catalogue client.

Fetches the full KEV feed once and caches it as a set of CVE IDs for O(1)
membership lookups, refreshed at most once per CACHE_EXPIRY_SECONDS.
"""

import logging
import random
import time
from datetime import datetime
from threading import Lock

import requests
from requests.adapters import HTTPAdapter

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"

logger = logging.getLogger(__name__)

# Cache configuration
CACHE_EXPIRY_SECONDS = 86400  # 1 day
_cache: set | None = None
_last_fetch_time: datetime | None = None
_cache_lock = Lock()

# Network resilience: a single failed request used to mean zero KEV data
# for up to 24 hours (the cache TTL). A short retry with backoff covers
# transient network blips without hammering CISA on persistent outages.
_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds; doubles each attempt: 2, 4, 8

# Reused HTTP session: avoids a fresh TCP+TLS handshake on every fetch
# (this client is called at most once per day per cache TTL, but the
# session is shared so any future callers — e.g. a manual refresh
# endpoint — also benefit from connection reuse rather than each paying
# full connection-setup cost).
_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=2, pool_maxsize=2)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)
_session.headers.update({"User-Agent": "ARGUS-Vulnerability-Scanner/1.0"})


def _fetch_kev_feed() -> set:
    """Fetch the KEV feed with retry/backoff. Raises on total failure."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = _session.get(KEV_URL, timeout=30)
            response.raise_for_status()
            data = response.json()
            return {v["cveID"] for v in data.get("vulnerabilities", [])}
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "[kev] Fetch attempt %d/%d failed: %s. Retrying in %.1fs.",
                    attempt + 1, _MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)
    raise last_exc


def _load_kev_ids() -> set:
    """Fetch KEV feed once and cache as a set for O(1) lookups.

    Thread-safe: a lock guards the check-then-fetch-then-store sequence so
    concurrent callers (the scanner runs asset scans concurrently) cannot
    each independently decide the cache is stale and all fire simultaneous
    fetches against CISA at once.
    """
    global _cache, _last_fetch_time
    with _cache_lock:
        if (
            _cache is not None
            and _last_fetch_time is not None
            and (datetime.now() - _last_fetch_time).total_seconds() < CACHE_EXPIRY_SECONDS
        ):
            return _cache

        try:
            _cache = _fetch_kev_feed()
            _last_fetch_time = datetime.now()
            return _cache
        except Exception as exc:
            logger.error("[kev] Failed to load KEV feed after retries: %s", exc)
            # Serve stale cache rather than an empty set if we have one —
            # an old-but-real KEV list is far more useful to risk scoring
            # than silently treating every CVE as non-KEV during an outage.
            if _cache is not None:
                logger.warning("[kev] Serving stale KEV cache from %s due to fetch failure.", _last_fetch_time)
                return _cache
            return set()


def is_kev(cve_id: str) -> bool:
    """Return True if cve_id is in the CISA KEV catalogue."""
    try:
        return cve_id in _load_kev_ids()
    except Exception as exc:
        logger.error("[kev] Failed to check KEV status for %s: %s", cve_id, exc)
        return False


def invalidate_cache():
    """Force a fresh fetch on the next call (call nightly to stay current)."""
    global _cache, _last_fetch_time
    with _cache_lock:
        _cache = None
        _last_fetch_time = None
