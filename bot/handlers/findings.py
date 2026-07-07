from telegram import Update
from telegram.ext import ContextTypes

from database.assets import get_asset
from database.matches import get_findings

SEVERITY_EMOJI = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🟢",
    "NONE": "⚪",
}

async def findings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /findings <asset_id>

    Show all known vulnerabilities for an asset, sorted by risk score.
    """
    if not context.args:
        await update.message.reply_text("Usage:\n/findings <asset_id>")
        return

    try:
        asset_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Asset ID must be a number.")
        return

    asset = get_asset(asset_id)
    if not asset:
        await update.message.reply_text("Asset not found.")
        return

    findings_data = get_findings(asset_id)
    if not findings_data:
        await update.message.reply_text(
            f"No findings yet for {asset['vendor']} {asset['product']}.\n"
            "Run /scan <id> to scan it first."
        )
        return

    formatted_findings = format_findings(findings_data, asset)
    await update.message.reply_text(formatted_findings[:4096])

def format_findings(findings_data, asset):
    lines = []
    for finding in findings_data:
        emoji = SEVERITY_EMOJI.get(finding.get("severity", "NONE"), "⚪")
        kev = " ⚠️KEV" if finding.get("kev") else ""
        lines.append(
            f"{emoji} {finding['cve_id']} | CVSS {finding['cvss']} | {finding['severity']}{kev}\n"
            f"   Risk: {finding['risk_score']}  |  First seen: "
            f"{finding['first_seen'].strftime('%Y-%m-%d') if finding['first_seen'] else '—'}"
        )

    header = (
        f"Findings — {asset['vendor']} {asset['product']} v{asset['version']}\n"
        f"Type: {asset.get('type', 'Unknown')}  |  Total: {len(findings_data)}\n\n"
    )
    return header + "\n\n".join(lines)
