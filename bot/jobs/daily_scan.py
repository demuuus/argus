# argus/bot/jobs/daily_scan.py
import asyncio
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from alerts.telegram_alert import send_document
from kev.clients import invalidate_cache
from reports.monthly import generate_monthly_report
from reports.weekly import generate_weekly_report
from scanner.scanner import scan_all_assets

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")

def _run_scheduled_scan():
    try:
        invalidate_cache()
        asyncio.run(scan_all_assets())
    except Exception as exc:
        logger.error(f"[scheduler] Daily scan failed: {exc}", exc_info=True)

def _risk_snapshot_job():
    """
    Phase 6 Requirement 5: record today's aggregate risk counts so trend
    questions ("how does this week compare to last week") have history to
    compare against. Scheduled 30 minutes after the daily scan (06:30) so
    the snapshot reflects post-scan state, not stale pre-scan numbers.
    """
    try:
        from database.risk_snapshots import record_today_snapshot
        record_today_snapshot()
    except Exception as exc:
        logger.error(f"[scheduler] Risk snapshot job failed: {exc}", exc_info=True)

def _weekly_report_job():
    try:
        path = generate_weekly_report()
        asyncio.run(send_document(path, caption="📄 Argus Weekly Report"))
    except Exception as exc:
        logger.error(f"[scheduler] Weekly report failed: {exc}", exc_info=True)

def _monthly_report_job():
    try:
        path = generate_monthly_report()
        asyncio.run(send_document(path, caption="📄 Argus Monthly Report"))
    except Exception as exc:
        logger.error(f"[scheduler] Monthly report failed: {exc}", exc_info=True)

def _ai_analysis_job():
    """
    Phase 6 Requirement 3: periodically drain the cve_ai_analysis pending
    queue. Runs every few minutes (not just once a day) so newly-scanned
    CVEs get analyzed promptly without ever blocking the scan itself —
    scanning only marks rows 'pending'; this job is what actually calls
    the LLM. Bounded batch size keeps each tick fast (see Ai/analyzer.py).
    """
    try:
        from Ai.analyzer import analyze_pending_batch
        summary = analyze_pending_batch()
        if summary["processed"]:
            logger.info(
                "[scheduler] AI analysis tick: %d processed, %d succeeded, %d failed",
                summary["processed"], summary["succeeded"], summary["failed"],
            )
    except Exception as exc:
        logger.error(f"[scheduler] AI analysis job failed: {exc}", exc_info=True)

def _ai_watchdog_job():
    """
    Phase 6 Requirement 7 (Production Hardening): sweep CVEs stuck in
    'processing' for more than 10 minutes back to 'pending' so they get
    retried. See database/cve_analysis.requeue_stale_processing_rows() for
    the full explanation — this closes the last known data-integrity gap
    in the AI analysis pipeline.
    """
    try:
        from database.cve_analysis import requeue_stale_processing_rows
        count = requeue_stale_processing_rows(stale_after_minutes=10)
        if count:
            logger.warning(
                "[scheduler] AI watchdog requeued %d stale 'processing' row(s).", count
            )
    except Exception as exc:
        logger.error(f"[scheduler] AI watchdog job failed: {exc}", exc_info=True)

def _chat_cache_purge_job():
    """
    Phase 6 Requirement 8 (Performance): delete expired ai_response_cache
    rows so the table never grows unbounded. Cache entries already stop
    being served once expired (get_cached_response checks expires_at), so
    this job is purely housekeeping, not a correctness requirement.
    """
    try:
        from database.chat_cache import purge_expired
        count = purge_expired()
        if count:
            logger.info("[scheduler] Purged %d expired chat cache row(s).", count)
    except Exception as exc:
        logger.error(f"[scheduler] Chat cache purge job failed: {exc}", exc_info=True)

def setup_scheduler():
    scheduler.add_job(_run_scheduled_scan, "cron", hour=6, minute=0, id="daily_scan", replace_existing=True)
    scheduler.add_job(_risk_snapshot_job, "cron", hour=6, minute=30, id="risk_snapshot", replace_existing=True)
    scheduler.add_job(_weekly_report_job, "cron", day_of_week="mon", hour=7, minute=0, id="weekly_report", replace_existing=True)
    scheduler.add_job(_monthly_report_job, "cron", day=1, hour=7, minute=0, id="monthly_report", replace_existing=True)
    scheduler.add_job(_ai_analysis_job, "interval", minutes=5, id="ai_analysis", replace_existing=True)
    scheduler.add_job(_ai_watchdog_job, "interval", minutes=5, id="ai_watchdog", replace_existing=True)
    scheduler.add_job(_chat_cache_purge_job, "interval", minutes=30, id="chat_cache_purge", replace_existing=True)

if __name__ == "__main__":
    import asyncio as _asyncio

    setup_scheduler()
    scheduler.start()
    try:
        while True:
            _asyncio.get_event_loop().run_until_complete(_asyncio.sleep(60))
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
