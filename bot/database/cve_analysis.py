"""
cve_analysis.py - cache layer for AI-generated CVE analysis (Requirement 2).

Each CVE is analyzed by the LLM at most once per (description, model) pair.
Results are stored in cve_ai_analysis and reused on every subsequent
dashboard view, chat question, or report, avoiding repeated LLM calls.

The cache is considered stale and eligible for re-analysis when:
    - No row exists yet for the CVE.
    - The stored description_hash no longer matches the current CVE
      description (the NVD description changed).
    - The stored model_used no longer matches the currently configured
      model (an upgrade should re-analyze for better quality).

This module handles the cache's read/write contract. The worker that
actually calls the LLM and is triggered on new-CVE-discovery lives in
Ai/analyzer.py.
"""

import hashlib
import logging
import os
from typing import Optional

from psycopg2.extras import RealDictCursor

from database.db import get_connection

logger = logging.getLogger(__name__)


def _hash_description(description: str) -> str:
    """Stable hash of a CVE description, used to detect upstream NVD edits."""
    return hashlib.sha256((description or "").encode("utf-8")).hexdigest()


def current_model_name() -> str:
    """
    The model identifier used for cache invalidation comparisons.
    Sourced from LLM_MODEL_NAME env var so changing models in production
    automatically invalidates old analyses without a manual cache flush.
    """
    return os.environ.get("LLM_MODEL_NAME", "default-local-llm")


def get_cached_analysis(cve_id: str) -> Optional[dict]:
    """Return the cached analysis row for a CVE, or None if it has never been analyzed."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM cve_ai_analysis WHERE cve_id = %s",
                (cve_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def is_stale(cve_id: str, current_description: str) -> bool:
    """
    True if the CVE has never been analyzed, the description has changed
    since the last analysis, or the model has changed since the last run.
    """
    row = get_cached_analysis(cve_id)
    if not row or row["status"] != "complete":
        return True
    if row["description_hash"] != _hash_description(current_description):
        return True
    if row["model_used"] != current_model_name():
        return True
    return False


def mark_pending(cve_id: str) -> None:
    """
    Insert (or reset) a 'pending' row for a CVE so the analysis worker
    picks it up on its next pass. Idempotent - safe to call repeatedly
    (e.g. once per scan) without creating duplicate work, and never
    downgrades an already-'complete' row back to 'pending'.
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cve_ai_analysis (cve_id, status)
                    VALUES (%s, 'pending')
                    ON CONFLICT (cve_id) DO UPDATE SET
                        status = CASE
                            WHEN cve_ai_analysis.status = 'complete' THEN cve_ai_analysis.status
                            ELSE 'pending'
                        END
                    """,
                    (cve_id,),
                )
    finally:
        conn.close()


def mark_processing(cve_id: str) -> None:
    """Flip a row to 'processing' so concurrent workers don't double-analyze it."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE cve_ai_analysis SET status = 'processing', updated_at = NOW() "
                    "WHERE cve_id = %s",
                    (cve_id,),
                )
    finally:
        conn.close()


def save_analysis(
    cve_id: str,
    description: str,
    summary: str = "",
    explanation: str = "",
    guidance: str = "",
    attack_scenario: str = "",
    business_impact: str = "",
    technical_impact: str = "",
    recommended_actions: str = "",
) -> None:
    """Persist a completed analysis, marking the cache entry 'complete'."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cve_ai_analysis (
                        cve_id, summary, explanation, guidance, attack_scenario,
                        business_impact, technical_impact, recommended_actions,
                        model_used, description_hash, status, analyzed_at,
                        retry_count, error_message
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'complete', NOW(), 0, NULL)
                    ON CONFLICT (cve_id) DO UPDATE SET
                        summary              = EXCLUDED.summary,
                        explanation          = EXCLUDED.explanation,
                        guidance             = EXCLUDED.guidance,
                        attack_scenario      = EXCLUDED.attack_scenario,
                        business_impact      = EXCLUDED.business_impact,
                        technical_impact     = EXCLUDED.technical_impact,
                        recommended_actions  = EXCLUDED.recommended_actions,
                        model_used           = EXCLUDED.model_used,
                        description_hash     = EXCLUDED.description_hash,
                        status               = 'complete',
                        analyzed_at           = NOW(),
                        updated_at            = NOW(),
                        retry_count           = 0,
                        error_message         = NULL
                    """,
                    (
                        cve_id, summary, explanation, guidance, attack_scenario,
                        business_impact, technical_impact, recommended_actions,
                        current_model_name(), _hash_description(description),
                    ),
                )
    finally:
        conn.close()


def mark_failed(cve_id: str, error_message: str) -> None:
    """Record an analysis failure and increment the retry counter (Requirement 3)."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cve_ai_analysis
                    SET status = 'failed',
                        error_message = %s,
                        retry_count = retry_count + 1,
                        updated_at = NOW()
                    WHERE cve_id = %s
                    """,
                    (error_message[:2000], cve_id),
                )
    finally:
        conn.close()


def get_pending_cves(limit: int = 10) -> list:
    """
    Return CVE IDs awaiting analysis, including failed ones with fewer
    than 3 retries (Requirement 3 - retry support). Used by Ai/analyzer.py.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT cve_id, retry_count
                FROM cve_ai_analysis
                WHERE status = 'pending'
                   OR (status = 'failed' AND retry_count < 3)
                ORDER BY updated_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def backfill_missing_analysis() -> int:
    """
    Queue every CVE in `cves` that has no row in `cve_ai_analysis` at all.

    Why this exists: queue_cve_for_analysis() is only called from
    scanner.py at the moment a *new* match is first discovered. Any CVE
    that already existed in the database before the Phase 6 analysis
    pipeline was deployed was never queued and would otherwise sit
    unanalyzed forever - confirmed in production (~550 CVEs, zero
    cve_ai_analysis rows). This is a one-time catch-up; new CVEs going
    forward are still queued normally by the scanner hook.

    Idempotent: uses the same INSERT ... ON CONFLICT DO NOTHING-equivalent
    logic as mark_pending() (it never downgrades a 'complete' row), so
    calling this on every app startup is safe and just becomes a no-op
    once the backlog is cleared.

    Returns the number of CVEs newly queued.
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cve_ai_analysis (cve_id, status)
                    SELECT DISTINCT c.cve_id, 'pending'
                    FROM cves c
                    LEFT JOIN cve_ai_analysis a ON a.cve_id = c.cve_id
                    WHERE a.cve_id IS NULL
                      -- Only queue CVEs that are still linked to at least
                      -- one asset that genuinely exists right now. Without
                      -- this check, this function (which runs on every app
                      -- startup to catch the original Phase 6 backlog)
                      -- would re-queue CVEs forever even after their only
                      -- asset was deleted -- confirmed in production: a
                      -- "Microsoft Windows 11" asset was deleted, but its
                      -- CVEs (e.g. CVE-2008-5745) kept getting re-analyzed
                      -- on every restart because this query never checked
                      -- whether the asset still existed.
                      AND EXISTS (
                          SELECT 1 FROM matches m
                          JOIN assets ast ON ast.id = m.asset_id
                          WHERE m.cve_id = c.cve_id
                      )
                    ON CONFLICT (cve_id) DO NOTHING
                    """
                )
                return cur.rowcount
    finally:
        conn.close()


def requeue_stale_processing_rows(stale_after_minutes: int = 10) -> int:
    """
    Phase 6 Requirement 7 (Production Hardening): sweep rows stuck in
    'processing' back to 'pending' if they've been there longer than
    `stale_after_minutes`.

    Why this exists: analyze_one() in Ai/analyzer.py calls mark_processing()
    *before* the LLM call, specifically so a crash mid-call leaves visible
    evidence (a 'processing' row) rather than silently looking like nothing
    happened. But get_pending_cves() only ever re-queues 'pending' or
    'failed' rows - it never looks at 'processing' - so if the app crashes,
    restarts, or the LLM call hangs past its timeout without the exception
    handler running, that CVE would otherwise stay stuck in 'processing'
    forever and never get retried. This was flagged as the top production
    gap in two previous sessions; this closes it.

    Called periodically by a scheduled job (see jobs/daily_scan.py) rather
    than relied upon to self-heal, since a stuck process can't un-stick
    itself.

    Returns the number of rows requeued.
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cve_ai_analysis
                    SET status = 'pending', updated_at = NOW()
                    WHERE status = 'processing'
                      AND updated_at < NOW() - (INTERVAL '1 minute' * %s)
                    """,
                    (stale_after_minutes,),
                )
                return cur.rowcount
    finally:
        conn.close()


def cleanup_orphaned_analysis() -> int:
    """
    Delete cve_ai_analysis rows for CVEs that are no longer linked to any
    currently-existing asset via matches.

    Why this exists: when an asset is deleted, its `matches` rows are
    correctly removed (ON DELETE CASCADE), but the CVE's own row in
    `cves` and `cve_ai_analysis` are intentionally left alone, since other
    assets might still reference the same CVE. The bug this fixes is
    different: backfill_missing_analysis() used to re-queue (and the
    background job would then re-analyze) ANY CVE missing an analysis
    row, with no check for whether it was still relevant to a real asset
    -- so a CVE whose only asset was deleted weeks ago would keep getting
    "found" and analyzed forever on every app restart. Confirmed in
    production: a deleted "Microsoft Windows 11" asset's CVEs (e.g.
    CVE-2008-5745) were still being freshly analyzed days after deletion.

    This is the cleanup half of that fix: it removes any analysis rows
    that are already orphaned from a PAST deletion (before this fix
    existed). Safe to run on every app startup -- it only ever deletes
    rows for CVEs with zero remaining matches, never touches a CVE that's
    still relevant to any asset, and is a pure no-op once the backlog of
    pre-existing orphans is cleared.

    Returns the number of orphaned rows deleted.
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM cve_ai_analysis a
                    WHERE NOT EXISTS (
                        SELECT 1 FROM matches m
                        JOIN assets ast ON ast.id = m.asset_id
                        WHERE m.cve_id = a.cve_id
                    )
                    """
                )
                return cur.rowcount
    finally:
        conn.close()
