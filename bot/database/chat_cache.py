"""
chat_cache.py - cache layer for repeated identical AI chat questions
(Phase 6, Requirement 8: Performance).

Why hash (question + ARGUS context) rather than just the question:
  The same literal question text ("which CVE should I fix first?") can
  have a genuinely different correct answer depending on the live state
  of your assets/findings/analysis data. Hashing the question together
  with the ARGUS context string that would have been built for it means
  the cache automatically invalidates the moment the underlying data
  changes (new scan, new AI analysis, new finding) - no manual
  cache-busting logic needed anywhere else in the codebase.

A short TTL is still enforced as a second safety net, in case two
different states of ARGUS data ever happen to hash-collide in a way that
produces a stale-but-still-matching answer for longer than is useful.
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from database.db import get_connection

logger = logging.getLogger(__name__)

# How long a cached answer stays valid even if its hash still matches.
# Short on purpose - ARGUS data (scans, AI analysis) can change within
# minutes, and a stale-but-cached answer about current risk is worse
# than the cost of one extra LLM call.
DEFAULT_TTL_MINUTES = 10


def _normalize_question(question: str) -> str:
    """
    Collapse whitespace and case so trivially different phrasings of the
    same question ("What CVEs are KEV?" vs "what cves are kev?") still
    hit the same cache entry.
    """
    return " ".join((question or "").strip().lower().split())


def make_cache_key(question: str, argus_context: str) -> str:
    """
    Build the cache key from the normalized question plus the exact
    ARGUS context string that was assembled for it. This is the
    mechanism that makes the cache auto-invalidate when ARGUS data
    changes - if the context differs at all (a new finding, a freshly
    completed analysis, etc.), the hash differs and it's a cache miss.
    """
    normalized = _normalize_question(question)
    combined = f"{normalized}\u0000{argus_context or ''}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def get_cached_response(cache_key: str) -> Optional[dict]:
    """
    Return {"response": str, "tokens": int} if a valid, non-expired
    cache entry exists, else None. Also increments hit_count for basic
    cache-effectiveness visibility.
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT response, tokens
                    FROM ai_response_cache
                    WHERE cache_key = %s AND expires_at > NOW()
                    """,
                    (cache_key,),
                )
                row = cur.fetchone()
                if not row:
                    return None

                cur.execute(
                    "UPDATE ai_response_cache SET hit_count = hit_count + 1 WHERE cache_key = %s",
                    (cache_key,),
                )
                return {"response": row[0], "tokens": row[1]}
    except Exception as exc:
        # A cache read failure must never break the chat — fall through
        # to a normal LLM call as if it were simply a cache miss.
        logger.warning("[chat_cache] Cache read failed: %s", exc)
        return None
    finally:
        conn.close()


def save_response(
    cache_key: str,
    question: str,
    response: str,
    tokens: int = 0,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> None:
    """Store a freshly-generated answer in the cache."""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_response_cache (cache_key, question, response, tokens, expires_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (cache_key) DO UPDATE SET
                        response   = EXCLUDED.response,
                        tokens     = EXCLUDED.tokens,
                        expires_at = EXCLUDED.expires_at,
                        hit_count  = 0
                    """,
                    (cache_key, question[:2000], response, tokens, expires_at),
                )
    except Exception as exc:
        # A cache write failure must never break the chat — the user
        # already has their real answer; just log and move on.
        logger.warning("[chat_cache] Cache write failed: %s", exc)
    finally:
        conn.close()


def purge_expired() -> int:
    """
    Delete expired cache rows. Called periodically by a scheduled job
    so the table doesn't grow unbounded with dead entries.
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_response_cache WHERE expires_at <= NOW()")
                return cur.rowcount
    finally:
        conn.close()
