from telegram import Update
from telegram.ext import ContextTypes

from database.db import get_connection
from nvd.client import check_nvd_api

def _check_postgres() -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status — real health checks and database statistics."""
    db_ok = _check_postgres()
    nvd_ok = check_nvd_api()

    assets = cves = matches = "—"
    if db_ok:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM assets")
                    assets = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM cves")
                    cves = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM matches")
                    matches = cur.fetchone()[0]
        except Exception:
            pass

    db_icon = "🟢" if db_ok else "🔴"
    nvd_icon = "🟢" if nvd_ok else "🔴"
    overall = "🟢" if db_ok and nvd_ok else "🔴"

    await update.message.reply_text(
        f"Argus Status {overall}\n\n"
        f"PostgreSQL: {db_icon} {'Online' if db_ok else 'Offline'}\n"
        f"NVD API:    {nvd_icon} {'Online' if nvd_ok else 'Offline'}\n\n"
        f"Assets:  {assets}\n"
        f"CVEs:    {cves}\n"
        f"Matches: {matches}\n"
    )