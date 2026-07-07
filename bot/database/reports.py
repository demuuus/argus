from database.db import get_connection
from psycopg2.extras import RealDictCursor


def save_report(report_type: str, file_path: str) -> int:
    """Insert a report record and return its ID."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reports (report_type, file_path)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (report_type, file_path),
                )
                return cur.fetchone()[0]
    finally:
        conn.close()


def get_reports(limit=20):
    """Return the most recent reports ordered by generation time desc."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, report_type, generated_at, file_path
                FROM reports
                ORDER BY generated_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_report(report_id: int):
    """Return a single report row by ID, or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, report_type, generated_at, file_path FROM reports WHERE id = %s",
                (report_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()
