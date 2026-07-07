from telegram import Update
from telegram.ext import ContextTypes
from database.assets import (
    get_asset,
    remove_asset
)

async def rm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if len(context.args) < 1:
        await update.message.reply_text(
            "Usage:\n/rm <id>"
        )
        return

    asset_id = context.args[0]
    asset = get_asset(asset_id)
    if not asset:
        await update.message.reply_text(
            "Asset not found."
        )
        return

    remove_asset(asset_id)
    await update.message.reply_text(
        f"Asset Removed\n\n"
        f"ID: {asset['id']}\n"
        f"{asset['vendor']} {asset['product']}"
    )