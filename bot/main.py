# main.py
import logging
import os
import traceback
from dotenv import load_dotenv

import psycopg2
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from handlers.add      import add
from handlers.asset    import asset
from handlers.cve      import cve
from handlers.edit     import edit
from handlers.findings import findings
from handlers.help     import help_command
from handlers.report   import report
from handlers.rm       import rm
from handlers.scan     import scan
from handlers.status   import status
from handlers.today    import today
from jobs.daily_scan   import scheduler, setup_scheduler

# Configure logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise RuntimeError("TOKEN environment variable is not set.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Argus Online 🟢")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(
        "Unhandled exception:\n%s",
        "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__)),
    )
    if isinstance(context.error, psycopg2.OperationalError):
        msg = f"❌ Database connection failed.\nDetail: {context.error}"
    elif isinstance(context.error, psycopg2.Error):
        msg = f"❌ Database error:\n{context.error}"
    else:
        msg = f"❌ Unexpected error:\n{context.error}"
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(msg)

def run_application():
    app = ApplicationBuilder().token(TOKEN).build()

    for command, handler in [
        ("start",    start),
        ("add",      add),
        ("asset",    asset),
        ("rm",       rm),
        ("help",     help_command),
        ("edit",     edit),
        ("cve",      cve),
        ("scan",     scan),
        ("status",   status),
        ("findings", findings),
        ("report",   report),
        ("today",    today),
    ]:
        app.add_handler(CommandHandler(command, handler))

    app.add_error_handler(error_handler)

    try:
        from database.cve_analysis import backfill_missing_analysis
        count = backfill_missing_analysis()
        if count:
            logger.info("Backfilled %d CVE(s) into the AI analysis queue.", count)
    except Exception as exc:
        logger.error("AI analysis backfill failed: %s", exc)

    try:
        from database.cve_analysis import cleanup_orphaned_analysis
        count = cleanup_orphaned_analysis()
        if count:
            logger.info("Cleaned up %d orphaned AI analysis row(s) (CVEs with no remaining asset).", count)
    except Exception as exc:
        logger.error("AI analysis orphan cleanup failed: %s", exc)

    setup_scheduler()
    scheduler.start()

    try:
        from database.risk_snapshots import record_today_snapshot
        record_today_snapshot()
        logger.info("Recorded an immediate risk snapshot for today at startup.")
    except Exception as exc:
        logger.error("Failed to record startup risk snapshot: %s", exc)

    logger.info("Argus started. Scheduler running.")
    app.run_polling()

if __name__ == "__main__":
    run_application()
