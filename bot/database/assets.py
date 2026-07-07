from database.db import get_connection
from psycopg2.extras import RealDictCursor

VALID_TYPES = {
    "Router", "Switch", "Firewall", "Server",
    "Workstation", "Printer", "Camera", "IoT",
    "NAS", "WAP", "PLC", "Unknown",
}

# Exposure: whether the asset is reachable from outside the organization's
# network. Binary and low-cardinality, so also enforced with a DB CHECK
# constraint (see database/migrate.py) as defense-in-depth — matching how
# matches.status is handled elsewhere in this schema.
VALID_EXPOSURES = {"Internal", "External"}

# Network function/role: what the asset DOES on the network, as distinct
# from its device category (`type`, e.g. "Firewall"/"Server"). A Firewall
# functions as a Gateway; a Workstation functions as an Endpoint — type
# and function are independent axes and both are useful for filtering.
# No DB CHECK constraint (unlike exposure): this list is more likely to
# grow over time, and type follows the same Python-only-validation
# precedent for the same reason.
VALID_FUNCTIONS = {
    "Gateway", "Endpoint", "Internal Server", "DMZ Host",
    "Load Balancer", "Jump Host", "Management", "Unknown",
}


def add_asset(vendor, product, version, asset_type="Unknown", search_keyword=None,
              city=None, country_code=None, exposure="Internal", function=None):
    """Insert a new asset and return its assigned ID.

    city/country_code are validated server-side against SUPPORTED_LOCATIONS
    (see config/locations.py) — an invalid combination is silently dropped
    to NULL rather than rejecting the whole asset creation, since city is
    optional. Callers that need hard validation/rejection (e.g. the web
    form) should validate before calling this and surface their own error.

    exposure defaults to 'Internal' (the safer default — see
    database/migrate.py's comment) and is coerced to a valid value the
    same way asset_type is, rather than rejecting the insert. function
    defaults to None (unclassified) since, unlike exposure, "no function
    assigned yet" is a legitimate state, not something to force a guess on.
    """
    from config.locations import is_valid_city

    asset_type = asset_type if asset_type in VALID_TYPES else "Unknown"
    exposure = exposure if exposure in VALID_EXPOSURES else "Internal"
    function = function if function in VALID_FUNCTIONS else None
    # Default search_keyword to "vendor product" if not provided
    if not search_keyword:
        search_keyword = f"{vendor} {product}"

    country_code = (country_code or "").strip().upper()[:2] or None
    city = (city or "").strip() or None
    if not is_valid_city(country_code, city):
        city, country_code = None, None

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO assets (vendor, product, version, type, search_keyword, city, country_code, exposure, function)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (vendor, product, version, asset_type, search_keyword, city, country_code, exposure, function),
                )
                return cur.fetchone()[0]
    finally:
        conn.close()


def get_all_assets():
    """Return lightweight list of all assets (id, vendor, product, version, type)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, vendor, product, version, type FROM assets ORDER BY id"
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_all_assets_full():
    """Return full asset rows, used by the scanner."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id, vendor, product, version, type,
                    search_keyword, location, owner, criticality,
                    notes, last_scan, created_at, city, country_code,
                    exposure, function
                FROM assets
                ORDER BY id
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_asset(asset_id):
    """Return a single asset row by ID, or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id, vendor, product, version, type,
                    search_keyword, location, owner, criticality,
                    notes, last_scan, created_at, city, country_code,
                    exposure, function
                FROM assets
                WHERE id = %s
                """,
                (asset_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def remove_asset(asset_id):
    """Delete an asset (cascade removes its matches)."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM assets WHERE id = %s", (asset_id,))
    finally:
        conn.close()


def update_asset(asset_id, location, owner, criticality, notes, asset_type=None,
                  search_keyword=None, city=None, country_code=None,
                  exposure=None, function=None):
    """Update editable fields on an asset.

    city/country_code follow the same server-side validation as add_asset():
    an invalid combination is dropped to NULL rather than rejecting the
    whole update, since this function is also used internally by code
    paths that don't pass city/country_code at all (those callers keep
    working unmodified — city/country_code default to None, which is a
    no-op against an asset that already has no location set, and is only
    a problem if it would silently CLEAR an existing valid city; see the
    app.py edit_asset route, which always passes the current values back
    through rather than omitting them, to avoid exactly that).

    asset_type/exposure/function all use the same "None means caller
    didn't specify this field, leave the existing DB value untouched"
    convention. This matters beyond app.py: handlers/edit.py's /edit
    Telegram command lets an operator omit the type entirely ("if omitted
    the existing type is preserved" — see its own docstring), relying on
    asset_type=None here to mean exactly that. exposure/function follow
    the same contract for consistency and so any future non-web caller
    can adopt the same "omit = don't touch" pattern.
    """
    from config.locations import is_valid_city

    country_code = (country_code or "").strip().upper()[:2] or None
    city = (city or "").strip() or None
    if not is_valid_city(country_code, city):
        city, country_code = None, None

    set_clauses = ["location = %s", "owner = %s", "criticality = %s",
                   "notes = %s", "search_keyword = %s",
                   "city = %s", "country_code = %s"]
    params = [location, owner, criticality, notes, search_keyword, city, country_code]

    if asset_type is not None:
        asset_type = asset_type if asset_type in VALID_TYPES else "Unknown"
        set_clauses.append("type = %s")
        params.append(asset_type)
    if exposure is not None:
        exposure = exposure if exposure in VALID_EXPOSURES else "Internal"
        set_clauses.append("exposure = %s")
        params.append(exposure)
    if function is not None:
        function = function if function in VALID_FUNCTIONS else None
        set_clauses.append("function = %s")
        params.append(function)

    params.append(asset_id)

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE assets SET {', '.join(set_clauses)} WHERE id = %s",
                    params,
                )
    finally:
        conn.close()


def update_last_scan(asset_id):
    """Stamp the last_scan column with the current time."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE assets SET last_scan = NOW() WHERE id = %s",
                    (asset_id,),
                )
    finally:
        conn.close()


def get_city_exposure_summary():
    """
    City Exposure Overview feature: one row per (country_code, city) with
    aggregated asset/finding/CVE/KEV/risk counts.

    Uses the REAL ARGUS schema (confirmed against the live database, not
    assumed from the feature spec's example):
        - cves.kev is the actual KEV boolean column (spec example used the
          placeholder name "is_kev", which does not exist in this project).
        - matches.id is the real findings primary key (one row per
          asset-CVE relationship) — there is no separate "findings" table.
        - matches.cve_id is TEXT, joined directly to cves.cve_id (also
          TEXT) — there is no separate numeric CVE id.

    COUNT(DISTINCT ...) is used throughout specifically so that one CVE
    matched to many assets in the same city counts as ONE unique CVE but
    MANY findings — this is the feature spec's own explicit example and
    the most important correctness requirement of this aggregation.

    A LEFT JOIN (not INNER JOIN) from assets to matches/cves means a city
    with assets but zero findings still appears, with zero counts, rather
    than disappearing — required by the spec.

    Cities with NULL or blank `city` are excluded entirely from this
    result (they represent unassigned assets, counted separately by
    get_unassigned_asset_count() below, never shown as a fake city row).
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    a.country_code,
                    a.city,
                    COUNT(DISTINCT a.id)                                       AS asset_count,
                    COUNT(DISTINCT m.id)                                       AS finding_count,
                    COUNT(DISTINCT c.cve_id)                                   AS unique_cve_count,
                    COUNT(DISTINCT CASE WHEN c.kev THEN c.cve_id END)          AS kev_count,
                    COALESCE(MAX(m.risk_score), 0)                            AS max_risk_score
                FROM assets a
                LEFT JOIN matches m ON m.asset_id = a.id
                LEFT JOIN cves c ON c.cve_id = m.cve_id
                WHERE a.city IS NOT NULL
                  AND TRIM(a.city) <> ''
                  AND a.country_code IS NOT NULL
                GROUP BY a.country_code, a.city
                ORDER BY max_risk_score DESC, finding_count DESC, asset_count DESC
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_unassigned_asset_count():
    """
    Count of assets with no city assigned at all. Returned separately
    from get_city_exposure_summary() per the feature spec — unassigned
    assets must never be rendered as a fake/blank city row or marker.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM assets
                WHERE city IS NULL OR TRIM(city) = '' OR country_code IS NULL
                """
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def get_patch_plan(scope="scheduled"):
    """
    Patch Planning view: one row per open finding, for scheduling.

    scope:
        "scheduled"   — only findings with a planned_patch_date set,
                        ordered soonest-first (the default "what's coming
                        up" view).
        "unscheduled" — open findings with NO planned_patch_date yet,
                        ordered by risk_score desc (highest-risk findings
                        that still need a patch date assigned, surfaced
                        first).
        "all"         — every open finding regardless of whether it has
                        a planned date, scheduled ones first (by date),
                        then unscheduled ones (by risk).

    Only findings with status NOT IN ('Resolved','Accepted Risk',
    'False Positive') are included — a resolved/accepted/false-positive
    finding has nothing left to plan, matching how /findings' own status
    filter and get_overdue_findings-style queries elsewhere in ARGUS
    already treat those three statuses as "closed out".
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            base_select = """
                SELECT
                    m.asset_id,
                    a.vendor,
                    a.product,
                    a.exposure,
                    a.function,
                    m.cve_id,
                    m.risk_score,
                    m.status,
                    m.due_date,
                    m.planned_patch_date,
                    m.patch_notes,
                    m.assigned_to,
                    m.assigned_team,
                    COALESCE(c.cvss, 0)             AS cvss,
                    COALESCE(c.kev, FALSE)          AS kev
                FROM matches m
                JOIN assets a ON m.asset_id = a.id
                LEFT JOIN cves c ON m.cve_id = c.cve_id
                WHERE m.status NOT IN ('Resolved', 'Accepted Risk', 'False Positive')
            """
            if scope == "scheduled":
                cur.execute(
                    base_select + " AND m.planned_patch_date IS NOT NULL "
                    "ORDER BY m.planned_patch_date ASC, m.risk_score DESC"
                )
            elif scope == "unscheduled":
                cur.execute(
                    base_select + " AND m.planned_patch_date IS NULL "
                    "ORDER BY m.risk_score DESC"
                )
            else:
                cur.execute(
                    base_select + " ORDER BY "
                    "(m.planned_patch_date IS NULL) ASC, m.planned_patch_date ASC, m.risk_score DESC"
                )
            return cur.fetchall()
    finally:
        conn.close()
