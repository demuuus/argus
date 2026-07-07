"""
/today — scan all assets and send a deduplicated summary.
"""
import logging
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

from scanner.scanner import scan_all_assets

logger = logging.getLogger(__name__)


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning all assets… this may take a minute.")

    try:
        results = await scan_all_assets()
    except Exception as exc:
        logger.error(f"[/today] scan_all_assets failed: {exc}")
        await update.message.reply_text(f"❌ Scan failed: {exc}")
        return

    if not results:
        await update.message.reply_text("No assets registered. Use /add to add one.")
        return

    # Deduplicate by keyword so multiple assets with the same keyword
    # (e.g. 4× "D-Link DIR-825") appear as one line with combined unique CVEs.
    by_keyword: dict = defaultdict(lambda: {"cves": set(), "new": set(), "error": None})
    for r in results:
        kw = r["keyword"]
        by_keyword[kw]["cves"].update(c["id"] for c in r.get("cves", []))
        by_keyword[kw]["new"].update(c["id"] for c in r.get("new_findings", []))
        if r.get("error") and not by_keyword[kw]["error"]:
            by_keyword[kw]["error"] = r["error"]

    total_unique_cves = len({cid for v in by_keyword.values() for cid in v["cves"]})
    total_new         = len({cid for v in by_keyword.values() for cid in v["new"]})
    total_errors      = sum(1 for v in by_keyword.values() if v["error"])

    lines = []
    for kw, v in sorted(by_keyword.items()):
        status = "❌" if v["error"] else "✅"
        lines.append(
            f"{status} {kw}  —  {len(v['cves'])} CVEs, {len(v['new'])} new"
        )

    summary = (
        f"📊 Scan Complete\n\n"
        f"Keywords scanned : {len(by_keyword)}\n"
        f"Unique CVEs      : {total_unique_cves}\n"
        f"New findings     : {total_new}\n"
        f"Errors           : {total_errors}\n\n"
        + "\n".join(lines)
    )

    await update.message.reply_text(summary[:4096])
