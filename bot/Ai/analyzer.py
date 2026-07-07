"""
analyzer.py - Automatic CVE Analysis Pipeline (Phase 6, Requirement 3).

Flow:
    New CVE discovered (scanner.py) -> mark_pending() in cve_ai_analysis
        -> analyze_pending_batch() picks it up on the next scheduler tick
        -> LLM call with NVD description + KEV/EPSS context (Requirement 6:
           model must reason from supplied data, not memory)
        -> save_analysis() on success / mark_failed() on failure (retried
           up to 3 times - see database/cve_analysis.get_pending_cves)

This module is intentionally synchronous (plain requests, no asyncio).
It is meant to be called from:
    - jobs/daily_scan.py, as a periodic APScheduler job, so it never
      blocks the Telegram bot, the Flask dashboard, or scan_asset().
    - manually / on-demand via analyze_one() for a single CVE (e.g. a
      future "Analyze now" button in the dashboard).

Production hardening (Requirement 7):
    - Every CVE is wrapped in its own try/except so one bad response
      never aborts the whole batch.
    - mark_processing() is set before the LLM call so a crash mid-call
      leaves a 'processing' row (not silently 'pending' forever). A
      future watchdog job can sweep stale 'processing' rows back to
      'pending' after a timeout - noted as a follow-up, out of scope here.
    - The LLM is asked to return strict JSON so the seven analysis
      fields can be split into separate columns; a malformed response
      is treated as a failure and retried, never partially saved.
"""

import json
import logging
import time
from typing import Optional

from database.cve_analysis import (
    get_pending_cves, mark_pending, mark_processing,
    mark_failed, save_analysis, is_stale,
)
from database.cves import get_cve
from Ai.llm import complete

logger = logging.getLogger(__name__)

# Cap how many CVEs one batch pass analyzes, so a sudden flood of new CVEs
# (e.g. a bulk asset import) can't make a single scheduler tick run for
# hours and starve other scheduled jobs (Requirement 7 - resource management).
DEFAULT_BATCH_SIZE = 5

# Small delay between LLM calls within a batch so a local LLM server
# (single-threaded llama.cpp, etc.) isn't hammered back-to-back.
INTER_REQUEST_DELAY_SECONDS = 1.0

ANALYSIS_SYSTEM_PROMPT = """You are ARGUS AI's CVE analysis engine.

You will be given a CVE ID, its NVD description, and ARGUS-specific
context (CVSS, KEV status, EPSS score). Using ONLY that supplied data -
never your own training memory of the CVE, which may be outdated or
wrong (Requirement 6: knowledge cutoff mitigation) - produce a structured
analysis.

If the supplied data does not contain enough information to answer a
field confidently, write exactly: "Information not available in ARGUS."
for that field. Never invent facts, CVSS vectors, affected versions, or
exploit details that were not in the supplied data.

Respond with ONLY a single JSON object, no markdown fences, no commentary,
with exactly these keys (all string values):
{
  "summary": "one or two sentence plain-language summary",
  "explanation": "what the vulnerability is and how it works",
  "guidance": "how to fix or mitigate it",
  "attack_scenario": "a realistic example of how an attacker could exploit it",
  "business_impact": "impact in business/operational terms",
  "technical_impact": "impact in technical terms (confidentiality/integrity/availability)",
  "recommended_actions": "concrete next steps, prioritized"
}
"""


def _build_user_prompt(cve_id: str, cve_row: dict) -> str:
    """Assemble the CVE context block the model must reason from."""
    description = cve_row.get("description") or "No description available."
    cvss        = cve_row.get("cvss")
    severity    = cve_row.get("severity") or "UNKNOWN"
    kev         = "YES - actively exploited in the wild" if cve_row.get("kev") else "No"
    epss        = cve_row.get("epss")
    epss_pct    = cve_row.get("epss_percentile")

    lines = [
        f"CVE ID: {cve_id}",
        f"NVD Description: {description}",
        f"CVSS Score: {cvss if cvss is not None else 'Information not available in ARGUS.'}",
        f"Severity: {severity}",
        f"CISA KEV (Known Exploited Vulnerability): {kev}",
        f"EPSS (exploit prediction score): "
        f"{epss if epss is not None else 'Information not available in ARGUS.'}"
        + (f" (percentile {epss_pct})" if epss_pct is not None else ""),
    ]
    return "\n".join(lines)


def _parse_llm_json(raw: str) -> Optional[dict]:
    """
    Extract a JSON object from the LLM's response, tolerating minor
    formatting noise (markdown fences, leading/trailing prose) that
    local models sometimes add despite instructions.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Fallback: find the first { ... last } span and try again - handles
    # models that wrap valid JSON in a sentence ("Here is the analysis: {...}").
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def analyze_one(cve_id: str) -> bool:
    """
    Analyze a single CVE and persist the result.
    Returns True on success, False on failure (and records the failure
    in cve_ai_analysis so get_pending_cves() can retry it later).
    """
    mark_processing(cve_id)

    cve_row = get_cve(cve_id)
    if not cve_row:
        mark_failed(cve_id, "CVE not found in cves table - cannot analyze.")
        logger.warning("[analyzer] %s not found in cves table", cve_id)
        return False

    description = cve_row.get("description") or ""

    try:
        user_prompt = _build_user_prompt(cve_id, cve_row)
        result = complete(ANALYSIS_SYSTEM_PROMPT, user_prompt, max_tokens=900)
        parsed = _parse_llm_json(result["answer"])

        if not parsed:
            mark_failed(cve_id, "LLM response was not valid JSON.")
            logger.error("[analyzer] %s - failed to parse LLM JSON output", cve_id)
            return False

        # Every field defaults to the explicit "not available" sentinel rather
        # than an empty string, so a partially-malformed model response still
        # produces a usable (and honest) row instead of blank dashboard cells.
        fallback = "Information not available in ARGUS."
        save_analysis(
            cve_id=cve_id,
            description=description,
            summary=parsed.get("summary", fallback),
            explanation=parsed.get("explanation", fallback),
            guidance=parsed.get("guidance", fallback),
            attack_scenario=parsed.get("attack_scenario", fallback),
            business_impact=parsed.get("business_impact", fallback),
            technical_impact=parsed.get("technical_impact", fallback),
            recommended_actions=parsed.get("recommended_actions", fallback),
        )
        logger.info("[analyzer] %s analyzed successfully", cve_id)
        return True

    except Exception as exc:
        logger.error("[analyzer] %s analysis failed: %s", cve_id, exc)
        mark_failed(cve_id, str(exc)[:2000])
        return False


def analyze_pending_batch(batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
    """
    Process up to `batch_size` pending/retryable CVEs.
    Designed to be called repeatedly (e.g. every few minutes by
    APScheduler) until the pending queue drains - each call only takes
    a small, bounded slice of work (Requirement 7: no infinite loops,
    bounded resource use per tick).

    Returns a summary dict: {"processed": int, "succeeded": int, "failed": int}
    """
    pending = get_pending_cves(limit=batch_size)
    summary = {"processed": 0, "succeeded": 0, "failed": 0}

    if not pending:
        logger.debug("[analyzer] No pending CVEs to analyze.")
        return summary

    logger.info("[analyzer] Starting batch of %d CVE(s)", len(pending))

    for i, row in enumerate(pending):
        cve_id = row["cve_id"]
        summary["processed"] += 1
        try:
            ok = analyze_one(cve_id)
            summary["succeeded" if ok else "failed"] += 1
        except Exception as exc:
            # analyze_one() already catches its own exceptions, but this
            # outer guard ensures a truly unexpected error (e.g. a bug in
            # mark_processing itself) still can't abort the whole batch.
            logger.error("[analyzer] Unexpected error analyzing %s: %s", cve_id, exc)
            summary["failed"] += 1

        if i < len(pending) - 1:
            time.sleep(INTER_REQUEST_DELAY_SECONDS)

    logger.info(
        "[analyzer] Batch complete: %d processed, %d succeeded, %d failed",
        summary["processed"], summary["succeeded"], summary["failed"],
    )
    return summary


def queue_cve_for_analysis(cve_id: str, description: str = "") -> None:
    """
    Convenience wrapper for scanner.py: queue a CVE for analysis only if
    it's actually stale (never analyzed, description changed, or model
    changed). Avoids re-queuing CVEs that already have a fresh, complete
    analysis - this is what prevents repeated LLM calls / wasted tokens
    on every scan (Requirement 2's caching goal, enforced at the
    queueing step rather than relying on the worker to skip them).
    """
    try:
        if is_stale(cve_id, description):
            mark_pending(cve_id)
    except Exception as exc:
        # Never let a queueing failure break the scan that's calling this.
        logger.warning("[analyzer] Failed to queue %s for analysis: %s", cve_id, exc)
