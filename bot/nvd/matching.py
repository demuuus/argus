# argus/bot/nvd/matching.py
"""
Single source of truth for turning a product query into the exact set of
NVD CVEs that authoritatively apply to it.

This used to be implemented separately (and differently) in three places:
- dashboard/app.py's Live NVD Search (/cves)
- nvd/client.py's get_cve_summary() -- used by the asset scanner and by the Telegram /cve command (handlers/cve.py)

Because each copy evolved independently, "Live NVD Search" and "Scan All
Assets" could -- and did -- return different results for the same product,
and neither reliably matched a manual NVD/Google lookup. The scanner in
particular never even used an asset's own `version` field when querying
NVD; it searched on vendor+product alone, unfiltered by version, using a
plain keywordSearch with no exact-match flag at all.

There is now exactly one implementation. All three callers use it. If this
needs to change again, it only needs to change here.

All HTTP calls go through nvd.http.get(), which retries NVD's 429/503
responses with backoff instead of treating them as a hard failure -- this
matters here more than almost anywhere else in ARGUS, because Scan All
Assets makes at least two NVD calls per asset (a CPE Dictionary lookup plus
a cpeName CVE lookup) across every asset in sequence, which is easily
enough to hit NVD's unauthenticated rate limit mid-scan.
"""
import re
from urllib.parse import quote_plus

from nvd.http import get as _http_get

_BASE_CVES = "https://services.nvd.nist.gov/rest/json/cves/2.0"
_BASE_CPES = "https://services.nvd.nist.gov/rest/json/cpes/2.0"

CVE_ID_RE = re.compile(r"^CVE-\d{4}-\d{4,10}$", re.IGNORECASE)

_VERSION_TOKEN_RE = re.compile(r"^\d+(\.\d+){1,3}[a-zA-Z0-9]*$")


def _normalize_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _describe_failure(r) -> str:
    """Turn a (possibly None) response into a human-readable error string."""
    if r is None:
        return "NVD request failed after retries (no response received)"
    return f"NVD returned HTTP {r.status_code}"


def split_keyword_version(keyword: str):
    """Split 'FortiOS 6.4.15' -> ('FortiOS', '6.4.15'). Returns (phrase, None)
    if there is no trailing dotted-version-looking token."""
    parts = keyword.strip().split()
    if len(parts) >= 2 and _VERSION_TOKEN_RE.match(parts[-1]):
        return " ".join(parts[:-1]), parts[-1]
    return keyword.strip(), None


def _version_key(version: str):
    """'6.4.15' -> (6, 4, 15) for tuple comparison. Non-numeric segments
    become 0 so odd vendor version strings never raise instead of compare."""
    key = []
    for seg in re.split(r"[.\-]", version):
        m = re.match(r"\d+", seg)
        key.append(int(m.group()) if m else 0)
    return tuple(key)


def version_in_range(version: str, cve_node: dict) -> bool:
    """Fallback-only check: does `version` fall inside a CVE's own attached
    CPE range data? Only used when the CPE Dictionary has no record for the
    product at all (see get_cves_for_product below). Returns True (keep the
    CVE) when there is no range data to check, so this never silently hides
    a real match -- it can only be over-inclusive, never under-inclusive."""
    vkey = _version_key(version)
    found_range_data = False

    for config in cve_node.get("configurations", []):
        for node in config.get("nodes", []):
            for match in node.get("cpeMatch", []):
                if not match.get("vulnerable", True):
                    continue

                start_inc = match.get("versionStartIncluding")
                start_exc = match.get("versionStartExcluding")
                end_inc = match.get("versionEndIncluding")
                end_exc = match.get("versionEndExcluding")

                if start_inc or start_exc or end_inc or end_exc:
                    found_range_data = True
                    ok = True
                    if start_inc and vkey < _version_key(start_inc):
                        ok = False
                    if start_exc and vkey <= _version_key(start_exc):
                        ok = False
                    if end_inc and vkey > _version_key(end_inc):
                        ok = False
                    if end_exc and vkey >= _version_key(end_exc):
                        ok = False
                    if ok:
                        return True
                else:
                    criteria_parts = match.get("criteria", "").split(":")
                    if len(criteria_parts) > 5:
                        cpe_version = criteria_parts[5]
                        if cpe_version not in ("*", "-"):
                            found_range_data = True
                            if _version_key(cpe_version) == vkey:
                                return True

    return not found_range_data


def nvd_cve_id_lookup(cve_id: str):
    """Exact single-CVE lookup via NVD's cveId parameter (not keywordSearch)."""
    url = f"{_BASE_CVES}?cveId={cve_id.upper()}"
    return _http_get(url, timeout=(10, 90))


def nvd_keyword_search(phrase: str, exact: bool, results_per_page: int, start_index: int = 0):
    """One NVD keywordSearch call. `exact` adds keywordExactMatch so a
    multi-word phrase is matched as a phrase, not an AND of loose words."""
    url = (
        f"{_BASE_CVES}?keywordSearch={quote_plus(phrase)}"
        f"&resultsPerPage={results_per_page}&startIndex={start_index}"
    )
    if exact:
        url += "&keywordExactMatch"
    return _http_get(url, timeout=(10, 90))


def resolve_cpe_vendor_products(phrase: str):
    """Look up (part, vendor, product) triples for a free-text product name
    via NVD's official CPE Dictionary, e.g. 'FortiOS' -> {('o','fortinet','fortios')}.

    IMPORTANT: NVD's /cpes keywordSearch matches against CPE *titles and
    reference links*, not just the product slug -- a loose search for
    'FortiOS' also returns genuinely different products like
    fortios-6k7k / fortios_carrier / fortios_vm (Fortinet ships several
    distinct CPE products that all mention 'FortiOS'). Blindly merging every
    match inflates results. To avoid that, a candidate is only kept if its
    CPE *product* field is an exact normalized match for the search phrase
    (or its last word, to allow a vendor prefix like 'Fortinet FortiOS') --
    never a substring/contains match.
    """
    words = phrase.strip().split()
    target_tokens = {_normalize_token(phrase)}
    if words:
        target_tokens.add(_normalize_token(words[-1]))

    def _query(exact):
        url = f"{_BASE_CPES}?keywordSearch={quote_plus(phrase)}&resultsPerPage=200"
        if exact:
            url += "&keywordExactMatch"
        return _http_get(url, timeout=(10, 60))

    triples = set()
    for exact in ([True, False] if " " in phrase else [False]):
        r = _query(exact)
        if r is None or not r.ok:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        for product in data.get("products", []):
            cpe_name = product.get("cpe", {}).get("cpeName", "")
            parts = cpe_name.split(":")
            if len(parts) > 4:
                part, vendor, product_slug = parts[2], parts[3], parts[4]
                if _normalize_token(product_slug) in target_tokens:
                    triples.add((part, vendor, product_slug))
        if triples:
            break

    return triples


def nvd_cves_by_cpe(part: str, vendor: str, product: str, version: str, start_index: int = 0):
    """CVEs that NVD itself has determined apply to this exact CPE + version,
    via the dedicated cpeName parameter -- not a text search."""
    cpe_name = f"cpe:2.3:{part}:{vendor}:{product}:{version}:*:*:*:*:*:*:*"
    url = f"{_BASE_CVES}?cpeName={quote_plus(cpe_name)}&resultsPerPage=200&startIndex={start_index}"
    return _http_get(url, timeout=(10, 90))


def get_cves_for_product(product_phrase: str, version: str | None):
    """Authoritative fetch for a known product name, optionally scoped to an
    exact version. Returns (vulnerabilities: list[dict], note: str|None,
    error: str|None).

    Primary path (version given, product found in the CPE Dictionary):
    resolves the product to its real CPE vendor:product, then lets NVD
    itself determine which CVEs apply to that exact version via cpeName --
    authoritative, not a guess.

    Fallback path (version given, product NOT in the CPE Dictionary):
    approximates using exact-phrase keywordSearch + whatever CPE range data
    each CVE happens to carry. `note` is set to tell the caller this is a
    weaker match.

    No-version path: a single exact-phrase/word keywordSearch across the
    whole product, unfiltered by version.
    """
    product_phrase = product_phrase.strip()
    if not product_phrase:
        return [], None, None

    usable_version = version.strip() if version and re.search(r"\d", version) else None

    if usable_version:
        triples = resolve_cpe_vendor_products(product_phrase)

        if triples:
            vulnerabilities = []
            seen_ids = set()
            for part, vendor, product in triples:
                fetched = 0
                while True:
                    r = nvd_cves_by_cpe(part, vendor, product, usable_version, start_index=fetched)
                    if r is None or not r.ok:
                        break
                    try:
                        page_data = r.json()
                    except Exception:
                        break
                    for item in page_data.get("vulnerabilities", []):
                        cid = item["cve"]["id"]
                        if cid not in seen_ids:
                            seen_ids.add(cid)
                            vulnerabilities.append(item)
                    total_results = page_data.get("totalResults", 0)
                    fetched += 200
                    if fetched >= total_results:
                        break
            return vulnerabilities, None, None

        # No CPE Dictionary entry for this product at all -- approximate.
        note = (
            f"No official CPE record found for '{product_phrase}' -- showing an approximate "
            f"match based on CVE description data instead of NVD's authoritative version ranges."
        )
        vulnerabilities = []
        fetched = 0
        MAX_FETCH = 500  # safety cap against pathologically broad base phrases

        while True:
            r = nvd_keyword_search(product_phrase, exact=True, results_per_page=100, start_index=fetched)
            if r is None or not r.ok:
                return [], None, _describe_failure(r)
            try:
                page_data = r.json()
            except Exception:
                return [], None, "NVD returned invalid response"

            vulnerabilities.extend(page_data.get("vulnerabilities", []))
            total_results = page_data.get("totalResults", 0)
            fetched += 100
            if fetched >= total_results or fetched >= MAX_FETCH:
                break

        filtered = [item for item in vulnerabilities if version_in_range(usable_version, item["cve"])]
        return filtered, note, None

    r = nvd_keyword_search(product_phrase, exact=True, results_per_page=100, start_index=0)
    if r is None or not r.ok:
        return [], None, _describe_failure(r)
    try:
        data = r.json()
    except Exception:
        return [], None, "NVD returned invalid response"
    return data.get("vulnerabilities", []), None, None


def get_cves_for_query(keyword: str):
    """Free-text entry point (Live NVD Search, Telegram /cve). Handles a
    literal CVE ID, a 'Product Version' phrase, or a bare product name.
    Returns (vulnerabilities, note, error) -- same contract as
    get_cves_for_product."""
    keyword = keyword.strip()
    if not keyword:
        return [], None, None

    if CVE_ID_RE.match(keyword):
        r = nvd_cve_id_lookup(keyword)
        if r is None or not r.ok:
            return [], None, _describe_failure(r)
        try:
            data = r.json()
        except Exception:
            return [], None, "NVD returned invalid response"
        return data.get("vulnerabilities", []), None, None

    base_phrase, version = split_keyword_version(keyword)
    return get_cves_for_product(base_phrase, version)
