from telegram import Update
from telegram.ext import ContextTypes

from database.assets import get_all_assets, get_asset


async def asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /asset          — list all assets
    /asset <id>     — show full details for one asset
    """
    if not context.args:
        assets = get_all_assets()
        if not assets:
            await update.message.reply_text("No assets found.")
            return

        lines = [
            f"{row['id']}. [{row['type']}] {row['vendor']} {row['product']} ({row['version']})"
            for row in assets
        ]
        await update.message.reply_text("Assets\n\n" + "\n".join(lines))
        return

    asset_id = context.args[0]
    row = get_asset(asset_id)
    if not row:
        await update.message.reply_text("Asset not found.")
        return

    last_scan = row["last_scan"].strftime("%Y-%m-%d %H:%M UTC") if row["last_scan"] else "Never"

    await update.message.reply_text(
        f"Asset Information\n\n"
        f"ID: {row['id']}\n"
        f"Vendor: {row['vendor']}\n"
        f"Product: {row['product']}\n"
        f"Version: {row['version']}\n"
        f"Type: {row['type']}\n\n"
        f"Location: {row['location'] or '—'}\n"
        f"Owner: {row['owner'] or '—'}\n"
        f"Priority: {row['criticality'] or '—'}\n\n"
        f"Last Scan: {last_scan}\n\n"
        f"Notes:\n{row['notes'] or '—'}"
    )