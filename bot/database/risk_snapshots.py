"""
risk_snapshots.py - historical daily aggregates for trend analysis
(Phase 6 Requirement 5: "how does this week compare to last week").

One row per calendar day, written by jobs/daily_scan.py's scheduled job.
Pre-aggregated, not a full table dump, so this stays small forever
(~365 rows/year) regardless of how many assets/findings ARGUS tracks.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from psycopg2.extras import RealDictCursor

from database.db import get_connection

logger = logging.getLogger(__name__)


def record_today_snapshot() -> None:
    """
    Compute today's aggregate counts from the live `matches`/`cves`/`assets`
    tables and upsert a row for today's date. Safe to call multiple times
    per day (e.g. if the scheduler restarts) - ON CONFLICT just refreshes
    today's numbers rather than creating duplicate rows.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_findings,
                    SUM(CASE WHEN m.status = 'Open' THEN 1 ELSE 0 END) AS open_findings,
                    SUM(CASE WHEN m.status = 'Resolved' THEN 1 ELSE 0 END) AS resolved_findings,
                    SUM(CASE WHEN c.kev = TRUE THEN 1 ELSE 0 END) AS kev_findings,
                    SUM(CASE WHEN m.status NOT IN ('Resolved','Accepted Risk','False Positive')
                    AND m.due_date < CURRENT_DATE THEN 1 ELSE 0 END) AS overdue_findings,
                    SUM(CASE WHEN UPPER(c.severity) = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_findings,
                    SUM(CASE WHEN UPPER(c.severity) = 'HIGH' THEN 1 ELSE 0 END) AS high_findings,
                    AVG(m.risk_score) AS avg_risk_score,
                    MAX(m.risk_score) AS max_risk_score
                FROM matches m
                JOIN cves c ON m.cve_id = c.cve_id
                """
            )
            row = cur.fetchone()
            (total, open_f, resolved, kev, overdue, critical, high, avg_risk, max_risk) = row

            cur.execute("SELECT COUNT(*) FROM assets")
            total_assets = cur.fetchone()[0]

            with conn:
                cur.execute(
                    """
                    INSERT INTO risk_snapshots (
                        snapshot_date, total_findings, open_findings, resolved_findings,
                        kev_findings, overdue_findings, critical_findings, high_findings,
                        avg_risk_score, max_risk_score, total_assets
                    )
                    VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (snapshot_date) DO UPDATE SET
                        total_findings    = EXCLUDED.total_findings,
                        open_findings     = EXCLUDED.open_findings,
                        resolved_findings = EXCLUDED.resolved_findings,
                        kev_findings      = EXCLUDED.kev_findings,
                        overdue_findings  = EXCLUDED.overdue_findings,
                        critical_findings = EXCLUDED.critical_findings,
                        high_findings     = EXCLUDED.high_findings,
                        avg_risk_score    = EXCLUDED.avg_risk_score,
                        max_risk_score    = EXCLUDED.max_risk_score,
                        total_assets      = EXCLUDED.total_assets
                    """,
                    (total or 0, open_f or 0, resolved or 0, kev or 0, overdue or 0, critical or 0, high or 0, avg_risk, max_risk, total_assets),
                )
            logger.info("[risk_snapshots] Recorded snapshot for %s", date.today())
    finally:
        conn.close()


def get_snapshot(snapshot_date: date) -> Optional[dict]:
    """Return the snapshot row for a specific date, or None if it doesn't exist."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM risk_snapshots WHERE snapshot_date = %s",
                (snapshot_date,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_latest_snapshot() -> Optional[dict]:
    """Return the most recent snapshot row, regardless of date."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM risk_snapshots ORDER BY snapshot_date DESC LIMIT 1"
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_week_over_week_comparison() -> Optional[dict]:
    """
    Compare the latest available snapshot against the snapshot closest to
    7 days before it. Returns None if fewer than 2 snapshots exist yet
    (e.g. on a brand-new deployment where the daily job hasn't run twice).

    Uses "closest to 7 days before", not an exact match, since the
    snapshot job might have missed a day (app downtime, etc.) - this
    keeps the comparison useful even with a slightly irregular history
    rather than failing outright.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM risk_snapshots ORDER BY snapshot_date DESC LIMIT 1"
            )
            latest = cur.fetchone()
            if not latest:
                return None

            target_date = latest["snapshot_date"] - timedelta(days=7)
            cur.execute(
                """
                SELECT * FROM risk_snapshots
                WHERE snapshot_date <= %s
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (target_date,),
            )
            previous = cur.fetchone()
            if not previous:
                return None

            return {"latest": latest, "previous": previous}
    finally:
        conn.close()
