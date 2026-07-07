"""
telegram_alert.py — fire-and-forget Telegram notifications.

Configured via environment variables:
    TOKEN    — Telegram bot token
    CHAT_ID  — target chat/channel ID
"""

import logging
import os

from dotenv import load_dotenv
from telegram import Bot

load_dotenv()

logger = logging.getLogger(__name__)

TOKEN   = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

_bot = Bot(TOKEN) if TOKEN else None

def validate_environment_variables():
    if not TOKEN:
        logger.error("Telegram bot token not configured.")
        raise EnvironmentError("Telegram bot token not configured.")
    if not CHAT_ID:
        logger.error("Telegram chat ID not configured.")
        raise EnvironmentError("Telegram chat ID not configured.")

async def send_alert(message: str) -> bool:
    validate_environment_variables()
    try:
        await _bot.send_message(chat_id=CHAT_ID, text=message)
        logger.info("[alert] Telegram alert sent: %s", message)
        return True
    except Exception as exc:
        logger.error("[alert] Failed to send Telegram alert: %s", exc)
        return False

async def send_document(pdf_path: str, caption: str = "") -> bool:
    validate_environment_variables()
    if not pdf_path:
        # Report generators (reports/daily.py, weekly.py, monthly.py,
        # yearly.py) return None on failure. Without this guard, open(None)
        # raises a generic TypeError that gets logged as an opaque
        # "Failed to send document: expected str, bytes or os.PathLike
        # object, not NoneType" — this makes the real cause explicit.
        logger.error("[alert] send_document called with no file path (report generation likely failed upstream).")
        return False
    try:
        with open(pdf_path, "rb") as f:
            await _bot.send_document(
                chat_id=CHAT_ID,
                document=f,
                caption=caption or None,
            )
        logger.info("[alert] Document sent: %s", pdf_path)
        return True
    except Exception as exc:
        logger.error("[alert] Failed to send document: %s", exc)
        return False
