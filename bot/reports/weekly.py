import logging
from datetime import datetime

from psycopg2.extras import RealDictCursor

from database.db import get_connection
from database.reports import save_report
from reports.pdf_generator import generate_pdf

logger = logging.getLogger(__name__)


def generate_weekly_report() -> str:
    """Generate and save the weekly report. Returns the file path or None on failure."""
    try:
        conn = get_connection()
        try:
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
                    WHERE m.first_seen >= NOW() - INTERVAL '7 days'
                    ORDER BY m.risk_score DESC
                    LIMIT 10
                """)
                findings = cur.fetchall()
        finally:
            conn.close()

        filename = f"weekly_{datetime.now().strftime('%Y_%m_%d')}.pdf"
        path = generate_pdf("Weekly", filename, assets=assets, cves=cves, kevs=kevs, findings=findings)
        save_report("weekly", path)
        return path
    except Exception as exc:
        logger.error(f"[reports] Failed to generate weekly report: {exc}")
        return None
