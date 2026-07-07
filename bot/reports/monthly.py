# argus/bot/reports/monthly.py
from datetime import datetime
from typing import Dict

from database.db import get_connection
from database.reports import save_report
from psycopg2.extras import RealDictCursor
from reports.pdf_generator import generate_pdf
import logging

logger = logging.getLogger(__name__)

def fetch_data(conn) -> Dict:
    """Fetch necessary data for the monthly report."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM assets")
        assets = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM cves")
        cves = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM cves WHERE kev = TRUE")
        kevs = cur.fetchone()[0]

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                m.asset_id,
                a.vendor,
                a.product,
                m.cve_id,
                m.risk_score,
                c.cvss,
                c.severity,
                c.kev
            FROM matches m
            JOIN assets a ON m.asset_id = a.id
            JOIN cves   c ON m.cve_id   = c.cve_id
            WHERE m.first_seen >= NOW() - INTERVAL '1 month'
            ORDER BY m.risk_score DESC
            LIMIT 20
        """)
        findings = cur.fetchall()
    return {
        "assets": assets,
        "cves": cves,
        "kevs": kevs,
        "findings": findings
    }

def generate_monthly_report() -> str:
    """Generate and save the monthly report."""
    try:
        conn = get_connection()
        try:
            data = fetch_data(conn)
        finally:
            conn.close()

        filename = f"monthly_{datetime.now().strftime('%Y_%m')}.pdf"
        path = generate_pdf("Monthly", filename, **data)
        save_report("monthly", path)
        return path
    except Exception as exc:
        logger.error(f"[reports] Failed to generate monthly report: {exc}")
        return None