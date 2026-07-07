from telegram import Update
from telegram.ext import ContextTypes

from nvd.client import get_cve_summary


_SEVERITY_EMOJI = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🟢",
    "NONE": "⚪",
}


async def cve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cve <keyword>  — search the NVD for CVEs matching a keyword.
    """
    if len(context.args) < 1:
        await update.message.reply_text("Usage:\n/cve <keyword>")
        return

    keyword = " ".join(context.args)
    results = get_cve_summary(keyword)

    if not results:
        await update.message.reply_text("No CVEs found.")
        return

    lines = []
    for item in results:
        emoji = _SEVERITY_EMOJI.get(item["severity"], "⚪")
        desc = item["description"][:180] + "…" if len(item["description"]) > 180 else item["description"]
        lines.append(
            f"{emoji} {item['id']} | CVSS {item['cvss']} | {item['severity']}\n{desc}"
        )

    message = f"CVE Search: {keyword}\n\n" + "\n\n".join(lines)
    await update.message.reply_text(message[:4096])