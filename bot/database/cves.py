import logging
from contextlib import contextmanager
from database.db import get_connection
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

def _severity_from_cvss(cvss: float) -> str:
    """Derive a severity label from a CVSS base score (NVD v3 scale)."""
    if cvss >= 9.0:
        return "CRITICAL"
    if cvss >= 7.0:
        return "HIGH"
    if cvss >= 4.0:
        return "MEDIUM"
    if cvss > 0:
        return "LOW"
    return "NONE"


@contextmanager
def get_db_connection():
    """Context manager to handle database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def save_cve(cve_id, cvss, kev, published, description, epss=0.0, epss_percentile=0.0):
    severity = _severity_from_cvss(float(cvss) if cvss else 0)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cves
                        (cve_id, cvss, kev, published, description, severity, epss, epss_percentile)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cve_id)
                    DO UPDATE SET
                        cvss = EXCLUDED.cvss,
                        kev = EXCLUDED.kev,
                        epss = EXCLUDED.epss,
                        epss_percentile = EXCLUDED.epss_percentile,
                        published = EXCLUDED.published,
                        description = EXCLUDED.description,
                        severity = EXCLUDED.severity
                    """,
                    (
                        cve_id, cvss, kev, published, description,
                        severity, epss, epss_percentile,
                    ),
                )
            # commit is handled by the get_db_connection() context manager

    except Exception as e:
        logger.error("[save_cve] Failed to save %s: %s", cve_id, e)
        raise

def get_cve(cve_id: str) -> dict:
    """
    Return a single CVE row by ID.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT cve_id, cvss, severity, kev, published, description, created_at, epss, epss_percentile
                    FROM cves
                    WHERE cve_id = %s
                    """,
                    (cve_id,),
                )
                return cur.fetchone()
    except Exception as e:
        logger.error(f"Failed to fetch CVE: {e}")
        raise