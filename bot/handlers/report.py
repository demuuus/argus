import os
from telegram import Update
from telegram.ext import ContextTypes

from reports.weekly import generate_weekly_report
from reports.monthly import generate_monthly_report
from database.db import get_connection
from database.reports import get_reports, get_report
from psycopg2.extras import RealDictCursor


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /report          — text summary (assets, CVEs, KEVs, top findings)
    /report week     — generate weekly PDF and send it
    /report month    — generate monthly PDF and send it
    /report list     — list the last 20 generated reports
    /report <id>     — re-send a previously generated PDF by report ID
    """
    arg = context.args[0].lower() if context.args else ""

    # ── list history ──────────────────────────────────────────────────────────
    if arg == "list":
        try:
            rows = get_reports()
            if not rows:
                await update.message.reply_text("No reports generated yet.")
                return
            lines = [
                f"{row['id']}. [{row['report_type']}] "
                f"{row['generated_at'].strftime('%Y-%m-%d %H:%M')}"
                for row in rows
            ]
            await update.message.reply_text("📁 Generated Reports\n\n" + "\n".join(lines))
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
        return

    # ── re-send by ID ─────────────────────────────────────────────────────────
    if arg.isdigit():
        try:
            report_id = int(arg)
            row = get_report(report_id)
            if not row:
                await update.message.reply_text("Report not found.")
                return

            file_path = row["file_path"]
            if not os.path.isabs(file_path):
                from reports.pdf_generator import GENERATED_REPORTS_DIR
                file_path = os.path.join(GENERATED_REPORTS_DIR, file_path)

            if not os.path.exists(file_path):
                await update.message.reply_text(
                    f"Report #{report_id} exists in the database but the PDF file is missing on disk."
                )
                return

            with open(file_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    caption=f"Report #{report_id} — {row['report_type']} — {row['generated_at'].strftime('%Y-%m-%d')}",
                )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
        return

    # ── generate daily PDF ────────────────────────────────────────────────────
    if arg == "day":
        try:
            await update.message.reply_text("⏳ Generating daily report…")
            from reports.daily import generate_daily_report
            path = generate_daily_report()
            if not path or not os.path.exists(path):
                await update.message.reply_text("Failed to generate daily report.")
                return
            with open(path, "rb") as f:
                await update.message.reply_document(document=f, caption="📄 Argus Daily Report")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
        return

    # ── generate weekly PDF ───────────────────────────────────────────────────
    if arg == "week":
        try:
            await update.message.reply_text("⏳ Generating weekly report…")
            path = generate_weekly_report()
            if not path or not os.path.exists(path):
                await update.message.reply_text("Failed to generate weekly report.")
                return
            with open(path, "rb") as f:
                await update.message.reply_document(document=f, caption="📄 Argus Weekly Report")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
        return

    # ── generate monthly PDF ──────────────────────────────────────────────────
    if arg == "month":
        try:
            await update.message.reply_text("⏳ Generating monthly report…")
            path = generate_monthly_report()
            if not path or not os.path.exists(path):
                await update.message.reply_text("Failed to generate monthly report.")
                return
            with open(path, "rb") as f:
                await update.message.reply_document(document=f, caption="📄 Argus Monthly Report")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
        return

    # ── generate yearly PDF ───────────────────────────────────────────────────
    if arg == "year":
        try:
            await update.message.reply_text("⏳ Generating yearly report…")
            from reports.yearly import generate_yearly_report
            path = generate_yearly_report()
            if not path or not os.path.exists(path):
                await update.message.reply_text("Failed to generate yearly report.")
                return
            with open(path, "rb") as f:
                await update.message.reply_document(document=f, caption="📄 Argus Yearly Report")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
        return

    # ── default: text summary ─────────────────────────────────────────────────
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
                    SELECT a.vendor, a.product, m.cve_id, c.cvss, c.severity, m.risk_score, c.kev
                    FROM matches m
                    JOIN assets a ON m.asset_id = a.id
                    JOIN cves   c ON m.cve_id   = c.cve_id
                    ORDER BY m.risk_score DESC
                    LIMIT 5
                """)
                findings = cur.fetchall()
        finally:
            conn.close()

        lines = [
            f"• [{row['severity'] or 'N/A'}] {row['vendor']} {row['product']} — "
            f"{row['cve_id']} | CVSS {row['cvss']} | Risk {row['risk_score']}"
            + (" ⚠️KEV" if row["kev"] else "")
            for row in findings
        ]

        message = (
            "📊 ARGUS REPORT\n\n"
            f"Assets:  {assets}\n"
            f"CVEs:    {cves}\n"
            f"KEVs:    {kevs}\n\n"
            "Top Findings\n\n"
            + ("\n".join(lines) if lines else "No findings yet.")
            + "\n\nTip: /report day · /report week · /report month · /report year · /report list · /report <id>"
        )
        await update.message.reply_text(message[:4096])
    except Exception as e:
        await update.message.reply_text(f"Error fetching summary: {e}")
