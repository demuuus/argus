from telegram import Update
from telegram.ext import ContextTypes

from database.assets import get_asset
from scanner.scanner import scan_asset

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /scan <asset_id>

    Triggers an on-demand vulnerability scan for the specified asset.
    All scanning logic lives in scanner/scanner.py.
    """
    if not context.args:
        await update.message.reply_text("Usage:\n/scan <asset_id>")
        return

    asset_id = context.args[0]
    asset = get_asset(asset_id)
    if not asset:
        await update.message.reply_text("Asset not found.")
        return

    await update.message.reply_text(
        f"🔍 Scanning {asset['vendor']} {asset['product']}…"
    )

    result = await scan_asset(asset)

    if result.get("error"):
        await update.message.reply_text(f"❌ Scan failed:\n{result['error']}")
        return

    if not result.get("cves"):
        await update.message.reply_text("No CVEs found for this asset.")
        return

    lines = [
        f"{c['id']} | CVSS {c['cvss']} | {c['severity']} | Risk {c['risk']}"
        + (" ⚠️ ACTIVE EXPLOIT" if c.get("kev") else "")
        for c in result["cves"]
    ]
    new_count = len(result.get("new_findings", []))
    header = (
        f"Scan Results — {result.get('keyword', 'Unknown')}\n\n"
        f"CVEs found: {len(result['cves'])}  |  New: {new_count}\n\n"
    )
    await update.message.reply_text((header + "\n".join(lines))[:4096])