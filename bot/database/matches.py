"""
matches.py — database operations for the matches (findings) table.

Schema expected:
    matches(
        id           SERIAL PRIMARY KEY,
        asset_id     INTEGER REFERENCES assets(id),
        cve_id       TEXT,
        risk_score   INTEGER,
        status       TEXT    NOT NULL DEFAULT 'Open',
        patched      BOOLEAN NOT NULL DEFAULT FALSE,
        first_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        resolved_at  TIMESTAMPTZ,
        due_date     DATE,
        assigned_to  TEXT,
        assigned_team TEXT,
        planned_patch_date DATE,
        patch_notes  TEXT,
        UNIQUE (asset_id, cve_id)
    )
"""

from database.db import get_connection
from psycopg2.extras import RealDictCursor
from datetime import date, timedelta


def _calc_due_date(cvss: float) -> date:
    """Return an SLA due-date based on CVSS score."""
    if cvss >= 9.0:
        return date.today() + timedelta(days=7)
    elif cvss >= 7.0:
        return date.today() + timedelta(days=30)
    elif cvss >= 4.0:
        return date.today() + timedelta(days=60)
    else:
        return date.today() + timedelta(days=90)


def save_match(asset_id: int, cve_id: str, risk_score: int, cvss: float = 0.0) -> bool:
    """
    Insert a match row, silently ignoring duplicates.
    Calculates and stores the SLA due_date based on CVSS.

    Returns True if this was a genuinely new match (row inserted), False
    if the (asset_id, cve_id) pair already existed (ON CONFLICT DO NOTHING
    skipped the insert). This lets callers detect "is this a new finding?"
    from the insert itself, instead of running a separate match_exists()
    SELECT before every insert — halving the DB round-trips in scan loops
    that process many CVEs per asset.
    """
    due_date = _calc_due_date(float(cvss or 0.0))
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO matches (asset_id, cve_id, risk_score, due_date)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (asset_id, cve_id) DO NOTHING
                    RETURNING id
                    """,
                    (asset_id, cve_id, risk_score, due_date),
                )
                return cur.fetchone() is not None
    finally:
        conn.close()


def match_exists(asset_id: int, cve_id: str) -> bool:
    """Return True if this asset/CVE pair is already recorded.

    Note: scanner.py no longer calls this before save_match() — use
    save_match()'s own return value to detect new-vs-duplicate inserts
    instead, which avoids a redundant SELECT per CVE. This function is
    kept for callers that need a pure existence check without inserting.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM matches WHERE asset_id = %s AND cve_id = %s",
                (asset_id, cve_id),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def get_findings(asset_id: int):
    """Return all findings for an asset ordered by risk desc."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    m.cve_id,
                    m.risk_score,
                    m.first_seen,
                    m.status,
                    m.patched,
                    m.resolved_at,
                    m.due_date,
                    m.assigned_to,
                    m.assigned_team,
                    m.planned_patch_date,
                    m.patch_notes,
                    COALESCE(c.cvss, 0)              AS cvss,
                    COALESCE(c.severity, 'UNKNOWN')  AS severity,
                    COALESCE(c.kev, FALSE)           AS kev,
                    COALESCE(c.description, 'No description available') AS description
                FROM matches m
                LEFT JOIN cves c ON m.cve_id = c.cve_id
                WHERE m.asset_id = %s
                ORDER BY m.risk_score DESC
                """,
                (asset_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_top_findings(limit: int = 10):
    """Return highest-risk matches across all assets, enriched with asset and CVE info."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    m.asset_id,
                    a.vendor,
                    a.product,
                    m.cve_id,
                    m.risk_score,
                    m.status,
                    c.cvss,
                    c.severity,
                    c.kev
                FROM matches m
                JOIN assets a ON m.asset_id = a.id
                JOIN cves   c ON m.cve_id   = c.cve_id
                ORDER BY m.risk_score DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def update_match_status(asset_id: int, cve_id: str, status: str) -> None:
    """Update the status of a match, and stamp resolved_at when Resolved."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                if status == "Resolved":
                    cur.execute(
                        """
                        UPDATE matches
                        SET status = %s, resolved_at = NOW()
                        WHERE asset_id = %s AND cve_id = %s
                        """,
                        (status, asset_id, cve_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE matches
                        SET status = %s, resolved_at = NULL
                        WHERE asset_id = %s AND cve_id = %s
                        """,
                        (status, asset_id, cve_id),
                    )
    finally:
        conn.close()


def update_match_assignment(asset_id: int, cve_id: str,
                            assigned_to: str, assigned_team: str) -> None:
    """Assign a finding to a person / team."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE matches
                    SET assigned_to = %s, assigned_team = %s
                    WHERE asset_id = %s AND cve_id = %s
                    """,
                    (assigned_to, assigned_team, asset_id, cve_id),
                )
    finally:
        conn.close()


def update_patch_plan(asset_id: int, cve_id: str,
                       planned_patch_date, patch_notes: str) -> None:
    """
    Set/clear the planned patch date and scheduling notes for a finding.

    Deliberately does NOT touch due_date (the auto-calculated SLA
    deadline) or status — this is purely the analyst's own scheduling
    decision, independent of both. Pass planned_patch_date=None to clear
    a previously-set date (e.g. a patch was rescheduled to "not yet
    decided").
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE matches
                    SET planned_patch_date = %s, patch_notes = %s
                    WHERE asset_id = %s AND cve_id = %s
                    """,
                    (planned_patch_date, patch_notes or None, asset_id, cve_id),
                )
    finally:
        conn.close()


def save_alert(asset_id: int, message: str) -> None:
    """Persist an alert to the alerts table for historical storage."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO alerts (asset_id, message) VALUES (%s, %s)",
                    (asset_id, message),
                )
    finally:
        conn.close()
