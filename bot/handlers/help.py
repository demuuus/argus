from telegram import Update
from telegram.ext import ContextTypes

from database.assets import VALID_TYPES

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    types_str = ", ".join(sorted(VALID_TYPES))
    help_text = (
        "🛡 *Argus — Vulnerability Tracker*\n\n"

        "─────────── *Assets* ───────────\n"
        "*/asset*\n"
        "  List all tracked assets.\n\n"

        "*/asset <id>*\n"
        "  Full details for one asset.\n\n"

        "*/add* `<vendor> \"<product>\" <version> \"<search_keyword>\" [type]`\n"
        "  Register a new asset. Type is optional (default: Unknown).\n"
        "  Valid types: " + types_str + "\n"
        "  _Example:_\n"
        "  `/add TP-Link \"Archer AX10\" 1.0 \"TP-Link Archer AX10\" Router`\n\n"

        "*/edit* `<id> <location> <owner> <criticality> [type] [notes...]`\n"
        "  Update asset metadata.\n"
        "  _Example:_\n"
        "  `/edit 3 DC-A1 alice High Firewall Edge gateway`\n\n"

        "*/rm <id>*\n"
        "  Delete an asset and all its findings.\n\n"

        "─────────── *Scanning* ───────────\n"
        "*/scan <id>*\n"
        "  On-demand NVD vulnerability scan. New findings trigger an alert.\n\n"

        "*/today*\n"
        "  Scan *all* assets at once and get a summary.\n\n"

        "─────────── *Findings* ───────────\n"
        "*/findings <id>*\n"
        "  List all CVEs for an asset with risk scores.\n\n"

        "*/cve <keyword>*\n"
        "  Search NVD for CVEs matching a keyword.\n\n"

        "─────────── *Reports* ───────────\n"
        "*/report*\n"
        "  Text summary (assets, CVEs, KEVs, top findings).\n\n"

        "*/report week*\n"
        "  Generate and send a weekly PDF report.\n\n"

        "*/report month*\n"
        "  Generate and send a monthly PDF report.\n\n"

        "*/report list*\n"
        "  List the last 20 generated reports.\n\n"

        "*/report <id>*\n"
        "  Re-send a previously generated report by its ID.\n\n"

        "─────────── *System* ───────────\n"
        "*/status*\n"
        "  Health check: PostgreSQL, NVD API, and database counts.\n\n"

        "*/start*\n"
        "  Confirm the bot is running.\n\n"

        "*/help*\n"
        "  Show this message."
    )

    await update.message.reply_text(help_text, parse_mode="Markdown")
