from telegram import Update
from telegram.ext import ContextTypes
import shlex

from database.assets import add_asset, VALID_TYPES

async def add(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    args = shlex.split(" ".join(context.args))

    if len(args) < 4:
        await update.message.reply_text(
            'Usage:\n'
            '/add <vendor> "<product>" <version> "<search_keyword>" [type]\n\n'
            'Example:\n'
            '/add Microsoft "Windows 11" 24H2 "Microsoft Windows 11" Workstation'
        )
        return

    vendor = args[0]
    product = args[1]
    version = args[2]
    search_keyword = args[3]

    asset_type = args[4] if len(args) >= 5 else "Unknown"

    if asset_type not in VALID_TYPES:
        await update.message.reply_text(
            f"Unknown type '{asset_type}'.\n\n"
            f"Valid types:\n{', '.join(sorted(VALID_TYPES))}"
        )
        return

    asset_id = add_asset(
        vendor,
        product,
        version,
        asset_type,
        search_keyword
    )

    await update.message.reply_text(
        f"✅ Asset Added\n\n"
        f"ID: {asset_id}\n"
        f"Vendor: {vendor}\n"
        f"Product: {product}\n"
        f"Version: {version}\n"
        f"Search: {search_keyword}\n"
        f"Type: {asset_type}"
    )