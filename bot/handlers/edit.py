from telegram import Update
from telegram.ext import ContextTypes

from database.assets import get_asset, update_asset, VALID_TYPES


async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /edit <id> Location Owner Criticality [Type] [Notes...]

    Notes (everything from position 5 onward) are optional.
    Type is optional; if omitted the existing type is preserved.

    Positional args:
        0  id
        1  location
        2  owner
        3  criticality
        4  type  (one of VALID_TYPES, or the start of notes if not recognised)
        5+ notes
    """
    if len(context.args) < 4:
        await update.message.reply_text(
            "Usage:\n/edit [ID] [Location] [Owner] [Criticality] [Type] [Notes]\n\n"
            f"Valid types:\n{', '.join(sorted(VALID_TYPES))}"
        )
        return

    asset_id   = context.args[0]
    location   = context.args[1]
    owner      = context.args[2]
    criticality = context.args[3]

    # Determine if arg[4] is a type or part of notes
    asset_type = None
    notes_start = 4
    if len(context.args) >= 5 and context.args[4] in VALID_TYPES:
        asset_type  = context.args[4]
        notes_start = 5

    notes = " ".join(context.args[notes_start:]) or None

    row = get_asset(asset_id)
    if not row:
        await update.message.reply_text("Asset not found.")
        return

    update_asset(asset_id, location, owner, criticality, notes, asset_type)

    # Re-fetch to show the final state
    updated = get_asset(asset_id)
    await update.message.reply_text(
        f"✅ Asset Updated\n\n"
        f"ID: {updated['id']}\n"
        f"Vendor: {updated['vendor']}\n"
        f"Product: {updated['product']}\n"
        f"Version: {updated['version']}\n"
        f"Type: {updated['type']}\n\n"
        f"Location: {updated['location'] or '—'}\n"
        f"Owner: {updated['owner'] or '—'}\n"
        f"Criticality: {updated['criticality'] or '—'}\n\n"
        f"Notes:\n{updated['notes'] or '—'}"
    )