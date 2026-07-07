"""
conversations.py — persistence layer for AI Security Copilot conversations.

Schema (see database/migrate.py):
    ai_conversations(id, username, title, created_at, updated_at, archived)
    ai_messages(id, conversation_id, role, content, tokens, created_at)

All functions open and close their own connection, matching the rest of
the ARGUS database layer (see database/reports.py, database/matches.py).
"""

import logging
from typing import Optional

from psycopg2.extras import RealDictCursor

from database.db import get_connection

logger = logging.getLogger(__name__)

# Cap how many messages are sent to the LLM as conversation history.
# Prevents unbounded context growth on very long conversations (Requirement 7).
MAX_HISTORY_MESSAGES = 20


def create_conversation(username: str, title: str = "New conversation") -> int:
    """Create a new conversation for a user and return its id."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_conversations (username, title)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (username, title),
                )
                return cur.fetchone()[0]
    finally:
        conn.close()


def list_conversations(username: str, limit: int = 50) -> list:
    """Return a user's non-archived conversations, most recently updated first."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM ai_conversations
                WHERE username = %s AND archived = FALSE
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (username, limit),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_conversation(conversation_id: int, username: str) -> Optional[dict]:
    """
    Return a single conversation if it belongs to `username`, else None.
    The username check prevents one user from reading another user's chat
    by guessing conversation IDs.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM ai_conversations
                WHERE id = %s AND username = %s
                """,
                (conversation_id, username),
            )
            return cur.fetchone()
    finally:
        conn.close()


def rename_conversation(conversation_id: int, username: str, new_title: str) -> bool:
    """Rename a conversation. Returns False if it doesn't belong to the user."""
    new_title = (new_title or "").strip()[:200] or "Untitled conversation"
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ai_conversations
                    SET title = %s, updated_at = NOW()
                    WHERE id = %s AND username = %s
                    """,
                    (new_title, conversation_id, username),
                )
                return cur.rowcount > 0
    finally:
        conn.close()


def delete_conversation(conversation_id: int, username: str) -> bool:
    """
    Permanently delete a conversation and all its messages (ON DELETE CASCADE).
    Returns False if it doesn't belong to the user.
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ai_conversations WHERE id = %s AND username = %s",
                    (conversation_id, username),
                )
                return cur.rowcount > 0
    finally:
        conn.close()


def add_message(conversation_id: int, role: str, content: str, tokens: int = 0) -> int:
    """Append a message to a conversation and bump the conversation's updated_at."""
    if role not in ("user", "assistant", "system"):
        raise ValueError(f"Invalid message role: {role!r}")

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_messages (conversation_id, role, content, tokens)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (conversation_id, role, content, tokens),
                )
                message_id = cur.fetchone()[0]
                cur.execute(
                    "UPDATE ai_conversations SET updated_at = NOW() WHERE id = %s",
                    (conversation_id,),
                )
                return message_id
    finally:
        conn.close()


def get_messages(conversation_id: int, username: str) -> list:
    """
    Return all messages for a conversation, oldest first, but only if the
    conversation belongs to `username`. Returns [] if not found/not owned —
    callers should check get_conversation() first if they need to
    distinguish "empty conversation" from "not found".
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT m.id, m.role, m.content, m.tokens, m.created_at
                FROM ai_messages m
                JOIN ai_conversations c ON m.conversation_id = c.id
                WHERE m.conversation_id = %s AND c.username = %s
                ORDER BY m.created_at ASC
                """,
                (conversation_id, username),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_recent_history_for_llm(conversation_id: int, username: str,
                                limit: int = MAX_HISTORY_MESSAGES) -> list:
    """
    Return the most recent `limit` messages formatted as
    [{"role": "user"/"assistant", "content": "..."}, ...] ready to pass
    to the LLM as conversation history. Ordered oldest-to-newest (the
    order an LLM expects), even though the query fetches newest-first.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT m.role, m.content
                FROM ai_messages m
                JOIN ai_conversations c ON m.conversation_id = c.id
                WHERE m.conversation_id = %s AND c.username = %s
                  AND m.role IN ('user', 'assistant')
                ORDER BY m.created_at DESC
                LIMIT %s
                """,
                (conversation_id, username, limit),
            )
            rows = cur.fetchall()
            rows.reverse()
            return [{"role": r["role"], "content": r["content"]} for r in rows]
    finally:
        conn.close()


def auto_title_from_message(message: str, max_len: int = 60) -> str:
    """Derive a short conversation title from the first user message."""
    message = (message or "").strip().replace("\n", " ")
    if not message:
        return "New conversation"
    if len(message) <= max_len:
        return message
    return message[:max_len].rsplit(" ", 1)[0] + "…"
