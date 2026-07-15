#!/usr/bin/env python3
"""
ARGUS Database Migration System
================================

Single-command installer and migration runner for ARGUS.

    python database/migrate.py

That is the entire install procedure. This script will, in order:

  1. Verify it can reach the PostgreSQL server at all (host/port/user/password).
  2. Check whether the configured database exists, and create it automatically
     if the connecting role has CREATEDB privileges (or explain how, if not).
  3. Connect to the target database.
  4. Ensure the `schema_version` bookkeeping table exists.
  5. Check whether the core tables (assets, cves, matches, alerts, reports,
     users) exist.
  6. If any core table is missing, execute schema.sql automatically — nobody
     ever has to run `psql -f schema.sql` by hand.
  7. Verify schema.sql actually produced the core tables/keys/indexes/views.
  8. Apply every incremental migration that has not already been recorded as
     applied in `schema_version` (idempotent — safe to run 1, 2, or 100
     times).
  9. Seed any required baseline data (currently: none — ARGUS creates its
     first user through the web UI's registration flow, see
     dashboard/app.py; this step exists so future baseline data has a home).
 10. Create/refresh every AI view with CREATE OR REPLACE VIEW.
 11. Verify every expected index exists (creating any still missing).
 12. Verify foreign keys.
 13. Verify check/unique constraints.
 14. Verify triggers (ARGUS defines none today; the hook exists for the day
     it does).
 15. Verify functions (same — none today).
 16. Print a checklist confirming every required table exists.
 17. Print a checklist confirming every required view exists.
 18. Report the applied schema version.
 19. Print a final summary.

Every step is idempotent. Every DDL statement checks for the object it's
about to touch before touching it. Running this script against a database
that is already fully migrated is a fast no-op that reconfirms everything
is healthy.

Flags:
    --verify-only        Skip all migration/creation steps; just run the
                          verification checklist (steps 12-19) against the
                          database as it currently stands.
    --dry-run             Show what would be applied, without applying it.
    --continue-on-error   Keep applying later migrations after one fails
                           instead of stopping (default: stop — a failed
                           migration usually means later ones would be
                           built on a broken assumption).
    --yes, -y              Don't prompt before creating the database.
"""

import argparse
import hashlib
import os
import re
import sys
import time
from typing import Optional

import psycopg2
import psycopg2.extensions
import psycopg2.errors
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_FILE = os.path.join(SCRIPT_DIR, "schema.sql")

# Overall ARGUS schema version. Bump this whenever a release adds a new
# batch of migrations, independent of how many individual ALTER/CREATE
# statements that batch contains.
CURRENT_SCHEMA_VERSION = "3.2"

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME", "argus_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD") or "",
    "port": int(os.getenv("DB_PORT", 5432)),
    "connect_timeout": 5,
}

# Tables that schema.sql itself is responsible for creating. If any of
# these is missing, schema.sql has never been applied to this database and
# must be run before anything else.
CORE_TABLES = ["assets", "cves", "matches", "alerts", "reports", "users"]

# The complete set of tables ARGUS requires to run, including the ones that
# only ever get created through incremental migrations (schema.sql was
# never updated to define these — see the note in MIGRATIONS below).
REQUIRED_TABLES = CORE_TABLES + [
    "ai_conversations",
    "ai_messages",
    "ai_response_cache",
    "cve_ai_analysis",
    "risk_snapshots",
]

REQUIRED_VIEWS = [
    "ai_dashboard",
    "ai_open_findings",
    "ai_asset_summary",
    "ai_vulnerability_summary",
]

# (table, column) pairs that must be backed by a FOREIGN KEY.
REQUIRED_FOREIGN_KEYS = [
    ("matches", "asset_id"),
    ("matches", "cve_id"),
    ("alerts", "asset_id"),
    ("ai_messages", "conversation_id"),
    ("cve_ai_analysis", "cve_id"),
]

# (table, column) pairs that must be backed by a CHECK constraint.
REQUIRED_CHECK_CONSTRAINTS = [
    ("assets", "exposure"),
    ("matches", "status"),
    ("cve_ai_analysis", "status"),
    ("ai_messages", "role"),
]

# (table, (columns...)) that must be backed by a UNIQUE constraint.
REQUIRED_UNIQUE_CONSTRAINTS = [
    ("matches", ("asset_id", "cve_id")),
    ("users", ("username",)),
    ("risk_snapshots", ("snapshot_date",)),
]

REQUIRED_INDEXES = [
    ("idx_matches_asset_id", "matches"),
    ("idx_matches_cve_id", "matches"),
    ("idx_matches_risk", "matches"),
    ("idx_matches_status", "matches"),
    ("idx_matches_due_date", "matches"),
    ("idx_matches_asset_cve", "matches"),
    ("idx_matches_planned_patch_date", "matches"),
    ("idx_assets_type", "assets"),
    ("idx_assets_exposure", "assets"),
    ("idx_assets_function", "assets"),
    ("idx_assets_city_country", "assets"),
    ("idx_cves_kev", "cves"),
    ("idx_cves_cvss", "cves"),
    ("idx_ai_conversations_username", "ai_conversations"),
    ("idx_ai_messages_conversation", "ai_messages"),
    ("idx_cve_ai_analysis_status", "cve_ai_analysis"),
    ("idx_risk_snapshots_date", "risk_snapshots"),
    ("idx_ai_response_cache_expires", "ai_response_cache"),
]


# ══════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════

class Log:
    """Minimal structured logging — no new dependency required."""

    @staticmethod
    def info(msg: str) -> None:
        print(f"  [INFO]    {msg}")

    @staticmethod
    def warn(msg: str) -> None:
        print(f"  [WARNING] {msg}")

    @staticmethod
    def ok(msg: str) -> None:
        print(f"  [SUCCESS] {msg}")

    @staticmethod
    def error(msg: str) -> None:
        print(f"  [ERROR]   {msg}")

    @staticmethod
    def step(n: int, total: int, title: str) -> None:
        print(f"\n[{n}/{total}] {title}")
        print("-" * 72)

    @staticmethod
    def check(label: str, passed: bool, detail: str = "") -> None:
        mark = "\u2713" if passed else "\u2717"
        suffix = f" ({detail})" if detail else ""
        print(f"  {mark} {label}{suffix}")

    @staticmethod
    def progress(current: int, total: int, label: str) -> None:
        width = 30
        filled = int(width * current / total) if total else width
        bar = "#" * filled + "-" * (width - filled)
        end = "\n" if current == total else ""
        print(f"\r  [{bar}] {current}/{total} {label}" + " " * 10, end=end, flush=True)


class MigrationError(Exception):
    """Raised to abort the run with a clear, already-logged reason."""


# ══════════════════════════════════════════════════════════════════════════
# Connection helpers
# ══════════════════════════════════════════════════════════════════════════

def _raw_connect(dbname: str, autocommit: bool = False):
    conn = psycopg2.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        dbname=dbname,
        connect_timeout=DB_CONFIG["connect_timeout"],
    )
    if autocommit:
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def classify_connection_error(exc: Exception) -> str:
    """Turn a raw psycopg2/libpq error into an actionable message."""
    text = str(exc).strip().lower()

    if "password authentication failed" in text:
        return (
            f"Wrong password for role '{DB_CONFIG['user']}'. Check DB_PASSWORD in your "
            f".env file against the actual PostgreSQL password for this role."
        )
    if "role" in text and "does not exist" in text:
        return (
            f"Role '{DB_CONFIG['user']}' does not exist in PostgreSQL. Check DB_USER in "
            f".env, or create the role: CREATE ROLE {DB_CONFIG['user']} WITH LOGIN "
            f"PASSWORD '...';"
        )
    if "could not translate host name" in text or "name or service not known" in text:
        return (
            f"Cannot resolve host '{DB_CONFIG['host']}'. Check DB_HOST in .env — is it "
            f"spelled correctly and reachable from this machine?"
        )
    if "connection refused" in text:
        return (
            f"Connection refused at {DB_CONFIG['host']}:{DB_CONFIG['port']}. Either "
            f"PostgreSQL is not running, is not listening on that port, or DB_PORT in "
            f".env is wrong (PostgreSQL's default is 5432)."
        )
    if "timeout expired" in text or "timed out" in text:
        return (
            f"Connection to {DB_CONFIG['host']}:{DB_CONFIG['port']} timed out. This "
            f"usually means a firewall is blocking the port, or DB_HOST points at an "
            f"address that isn't actually reachable from here."
        )
    if "no pg_hba.conf entry" in text:
        return (
            "PostgreSQL rejected the connection per its pg_hba.conf rules for this "
            "host/user/database combination. An admin needs to add a matching entry "
            "to pg_hba.conf and reload PostgreSQL."
        )
    if "database" in text and "does not exist" in text:
        return f"Database '{DB_CONFIG['database']}' does not exist yet (this is handled automatically below)."

    return f"Unrecognized connection failure: {exc}"


def get_maintenance_connection():
    """
    Connect to a maintenance database (not the ARGUS database itself, which
    may not exist yet) so we can check for / create the target database.
    Tries 'postgres' first (present on virtually every install), then
    falls back to 'template1'.
    """
    last_exc: Optional[Exception] = None
    for maint_db in ("postgres", "template1"):
        try:
            conn = _raw_connect(maint_db, autocommit=True)
            return conn
        except psycopg2.OperationalError as exc:
            last_exc = exc
            # "database does not exist" just means this particular
            # maintenance db isn't there — try the next one. Anything else
            # (auth, host, port, timeout) will fail identically against
            # every database, so surface it immediately.
            if "does not exist" not in str(exc).lower():
                break
    raise MigrationError(classify_connection_error(last_exc))


# ══════════════════════════════════════════════════════════════════════════
# Helper predicates — the vocabulary the rest of this script is built on
# ══════════════════════════════════════════════════════════════════════════

def database_exists(maint_conn, name: str) -> bool:
    with maint_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        return cur.fetchone() is not None


def schema_exists(cur, name: str = "public") -> bool:
    cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,))
    return cur.fetchone() is not None


def table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )
    return cur.fetchone() is not None


def view_exists(cur, view: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.views "
        "WHERE table_schema = 'public' AND table_name = %s",
        (view,),
    )
    return cur.fetchone() is not None


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return cur.fetchone() is not None


def index_exists(cur, index_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM pg_class WHERE relname = %s AND relkind = 'i'",
        (index_name,),
    )
    return cur.fetchone() is not None


def function_exists(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (name,))
    return cur.fetchone() is not None


def trigger_exists(cur, table: str, trigger_name: Optional[str] = None) -> bool:
    if trigger_name:
        cur.execute(
            "SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE c.relname = %s AND t.tgname = %s AND NOT t.tgisinternal",
            (table, trigger_name),
        )
    else:
        cur.execute(
            "SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE c.relname = %s AND NOT t.tgisinternal",
            (table,),
        )
    return cur.fetchone() is not None


def foreign_key_exists(cur, table: str, column: str) -> bool:
    """
    Checks by (table, column), not by constraint name — several of ARGUS's
    foreign keys are declared inline in CREATE TABLE without an explicit
    name, so Postgres auto-generates one. Matching on the actual key
    column is the only way that's robust regardless of naming.
    """
    cur.execute(
        """
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
        WHERE c.contype = 'f' AND t.relname = %s AND a.attname = %s
        LIMIT 1
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def check_constraint_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'c' AND t.relname = %s
        """,
        (table,),
    )
    return any(column in (row[0] or "") for row in cur.fetchall())


def unique_constraint_exists(cur, table: str, columns) -> bool:
    cur.execute(
        """
        SELECT c.oid, array_agg(a.attname)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
        WHERE c.contype = 'u' AND t.relname = %s
        GROUP BY c.oid
        """,
        (table,),
    )
    wanted = tuple(sorted(columns))
    for _, cols in cur.fetchall():
        if tuple(sorted(cols)) == wanted:
            return True
    return False


def constraint_exists(cur, constraint_name: str) -> bool:
    """Lookup by explicit name, for the constraints ARGUS does name."""
    cur.execute("SELECT 1 FROM pg_constraint WHERE conname = %s", (constraint_name,))
    return cur.fetchone() is not None


# ══════════════════════════════════════════════════════════════════════════
# Step 6/7: schema.sql execution + verification
# ══════════════════════════════════════════════════════════════════════════

def create_database_if_missing(maint_conn, name: str, assume_yes: bool) -> bool:
    """
    Returns True if the database exists afterwards (whether it already did,
    or was just created).
    """
    if database_exists(maint_conn, name):
        Log.ok(f"Database '{name}' already exists.")
        return True

    Log.warn(f"Database '{name}' does not exist.")
    if not assume_yes:
        try:
            answer = input(f"  Create database '{name}' now? [Y/n] ").strip().lower()
        except EOFError:
            answer = "y"
        if answer not in ("", "y", "yes"):
            Log.error("Aborted by user.")
            return False

    try:
        with maint_conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}" OWNER "{DB_CONFIG["user"]}"')
        Log.ok(f"Created database '{name}', owned by '{DB_CONFIG['user']}'.")
        return True
    except psycopg2.errors.InsufficientPrivilege:
        Log.error(
            f"Role '{DB_CONFIG['user']}' does not have CREATEDB privileges. "
            f"Ask a PostgreSQL admin to run:\n"
            f"    CREATE DATABASE {name} OWNER {DB_CONFIG['user']};\n"
            f"then re-run this script."
        )
        return False
    except Exception as exc:
        Log.error(f"Failed to create database '{name}': {exc}")
        return False


def execute_schema(conn) -> None:
    """
    Execute schema.sql in full. schema.sql is written entirely with
    CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / DO $$ ... IF
    NOT EXISTS $$ guards, so it is always safe to run — on a brand-new
    database it creates everything from scratch; on an existing one, every
    statement is a no-op except the ones repairing genuinely missing
    pieces.
    """
    if not os.path.isfile(SCHEMA_FILE):
        raise MigrationError(f"schema.sql not found at {SCHEMA_FILE}")

    with open(SCHEMA_FILE, "r", encoding="utf-8") as fh:
        sql = fh.read()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        Log.ok("schema.sql applied successfully.")
    except Exception as exc:
        conn.rollback()
        raise MigrationError(f"Failed to execute schema.sql: {exc}") from exc


def verify_schema(cur, tables) -> list:
    """Returns the list of tables from `tables` that are still missing."""
    return [t for t in tables if not table_exists(cur, t)]


# ══════════════════════════════════════════════════════════════════════════
# Step 4: schema_version bookkeeping table
# ══════════════════════════════════════════════════════════════════════════

SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    id             SERIAL PRIMARY KEY,
    version        TEXT        NOT NULL,
    migration_name TEXT        NOT NULL UNIQUE,
    applied_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms    INTEGER,
    checksum       TEXT,
    status         TEXT        NOT NULL DEFAULT 'success'
                   CHECK (status IN ('success', 'failed'))
)
"""


def ensure_schema_version_table(conn) -> None:
    with conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_VERSION_DDL)


# ══════════════════════════════════════════════════════════════════════════
# Step 8: incremental migrations
#
# Every entry is (name, sql). `name` is the idempotency key stored in
# schema_version.migration_name — it must never change once released, or a
# database that already applied it will try to apply it again under a new
# name. Each migration is executed in its own transaction: a failure rolls
# back only that migration, not any migration applied earlier in this run.
# ══════════════════════════════════════════════════════════════════════════

MIGRATIONS = [
    ("assets.type column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT 'Unknown'"),
    ("assets.last_scan column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS last_scan TIMESTAMPTZ"),
    ("assets.search_keyword column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS search_keyword TEXT"),
    ("cves.severity column",
     "ALTER TABLE cves ADD COLUMN IF NOT EXISTS severity TEXT"),
    ("cves.epss column",
     "ALTER TABLE cves ADD COLUMN IF NOT EXISTS epss NUMERIC(8,6)"),
    ("cves.epss_percentile column",
     "ALTER TABLE cves ADD COLUMN IF NOT EXISTS epss_percentile NUMERIC(8,6)"),
    # schema.sql's CREATE TABLE cves defines created_at, but on a database
    # whose cves table pre-dates that column, CREATE TABLE IF NOT EXISTS is
    # a no-op against the already-existing table, so it never gets added.
    # Without this repair, any query selecting created_at from cves (e.g.
    # database/cves.py's get_cve()) fails with 'column "created_at" does
    # not exist.'
    ("cves.created_at column",
     "ALTER TABLE cves ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    ("matches UNIQUE (asset_id, cve_id) constraint",
     """
     DO $$
     BEGIN
         IF NOT EXISTS (
             SELECT 1 FROM pg_constraint WHERE conname = 'matches_asset_id_cve_id_key'
         ) THEN
             DELETE FROM matches a USING matches b
             WHERE a.id > b.id AND a.asset_id = b.asset_id AND a.cve_id = b.cve_id;
             ALTER TABLE matches ADD CONSTRAINT matches_asset_id_cve_id_key UNIQUE (asset_id, cve_id);
         END IF;
     END $$
     """),
    ("matches.first_seen column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()"),

    # ── Phase 2: Remediation tracking ────────────────────────────────────
    ("matches.status column",
     """
     DO $$
     BEGIN
         IF NOT EXISTS (
             SELECT 1 FROM information_schema.columns
             WHERE table_name='matches' AND column_name='status'
         ) THEN
             ALTER TABLE matches ADD COLUMN status TEXT NOT NULL DEFAULT 'Open'
                 CHECK (status IN ('Open','In Progress','Resolved','Accepted Risk','False Positive'));
         END IF;
     END $$
     """),
    ("matches.resolved_at column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ"),
    ("matches.due_date column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS due_date DATE"),
    ("matches.patched column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS patched BOOLEAN NOT NULL DEFAULT FALSE"),

    # ── Phase 2: Ownership & Assignment ──────────────────────────────────
    ("matches.assigned_to column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS assigned_to TEXT"),
    ("matches.assigned_team column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS assigned_team TEXT"),

    # ── Backfill SLA due_date for existing Open findings ─────────────────
    ("backfill matches.due_date from cvss",
     """
     UPDATE matches m
     SET due_date = CASE
         WHEN c.cvss >= 9.0 THEN m.first_seen::date + INTERVAL '7 days'
         WHEN c.cvss >= 7.0 THEN m.first_seen::date + INTERVAL '30 days'
         WHEN c.cvss >= 4.0 THEN m.first_seen::date + INTERVAL '60 days'
         ELSE                     m.first_seen::date + INTERVAL '90 days'
     END
     FROM cves c
     WHERE m.cve_id = c.cve_id
       AND m.due_date IS NULL
     """),

    # ── System tables ─────────────────────────────────────────────────────
    ("alerts table",
     """
     CREATE TABLE IF NOT EXISTS alerts (
         id       SERIAL PRIMARY KEY,
         asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
         message  TEXT        NOT NULL,
         sent_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )
     """),
    ("reports table",
     """
     CREATE TABLE IF NOT EXISTS reports (
         id           SERIAL PRIMARY KEY,
         report_type  VARCHAR(20),
         generated_at TIMESTAMP NOT NULL DEFAULT NOW(),
         file_path    TEXT      NOT NULL
     )
     """),
    ("users table",
     """
     CREATE TABLE IF NOT EXISTS users (
         id            SERIAL PRIMARY KEY,
         username      TEXT UNIQUE NOT NULL,
         password_hash TEXT NOT NULL,
         role          TEXT NOT NULL DEFAULT 'viewer',
         created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )
     """),

    # ── Indexes ───────────────────────────────────────────────────────────
    ("index matches(asset_id)",   "CREATE INDEX IF NOT EXISTS idx_matches_asset_id ON matches(asset_id)"),
    ("index matches(cve_id)",     "CREATE INDEX IF NOT EXISTS idx_matches_cve_id ON matches(cve_id)"),
    ("index matches(risk_score)", "CREATE INDEX IF NOT EXISTS idx_matches_risk ON matches(risk_score DESC)"),
    ("index matches(status)",     "CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)"),
    ("index matches(due_date)",   "CREATE INDEX IF NOT EXISTS idx_matches_due_date ON matches(due_date)"),
    # Composite index used by dashboard queries that filter matches by
    # asset AND status together. Present in schema.sql's Phase 2 block but
    # missing from earlier versions of this migration list, so a database
    # that was only ever migrated (never had schema.sql applied fresh)
    # would silently never get it.
    ("index matches(asset_id, status) composite",
     "CREATE INDEX IF NOT EXISTS idx_matches_asset_cve ON matches(asset_id, status)"),
    ("index assets(type)",        "CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(type)"),
    ("index cves(kev) partial",   "CREATE INDEX IF NOT EXISTS idx_cves_kev ON cves(kev) WHERE kev = TRUE"),
    ("index cves(cvss)",          "CREATE INDEX IF NOT EXISTS idx_cves_cvss ON cves(cvss DESC)"),

    # ── Back-fill data ────────────────────────────────────────────────────
    ("back-fill cves.severity from cvss",
     """
     UPDATE cves SET severity =
         CASE
             WHEN cvss >= 9.0 THEN 'CRITICAL'
             WHEN cvss >= 7.0 THEN 'HIGH'
             WHEN cvss >= 4.0 THEN 'MEDIUM'
             WHEN cvss >  0   THEN 'LOW'
             ELSE 'NONE'
         END
     WHERE severity IS NULL
     """),
    ("back-fill assets.search_keyword",
     "UPDATE assets SET search_keyword = vendor || ' ' || product WHERE search_keyword IS NULL"),

    # ── Phase 6: AI Security Copilot — persistent conversations ──────────
    # NOTE: schema.sql never defines these tables at all — they only exist
    # via this migration list (and the mirrored _ensure_schema() in
    # dashboard/app.py). An earlier ad-hoc setup created ai_conversations /
    # ai_messages with a different shape (user_id INTEGER instead of
    # username TEXT, no updated_at/archived/tokens columns).
    # CREATE TABLE IF NOT EXISTS silently no-ops against that pre-existing
    # table, so the ALTER TABLE ... ADD COLUMN IF NOT EXISTS statements
    # below are what actually repair a live database.
    ("ai_conversations table",
     """
     CREATE TABLE IF NOT EXISTS ai_conversations (
         id          SERIAL PRIMARY KEY,
         username    TEXT        NOT NULL,
         title       TEXT        NOT NULL DEFAULT 'New conversation',
         created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         archived    BOOLEAN     NOT NULL DEFAULT FALSE
     )
     """),
    ("repair ai_conversations.username column",
     "ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS username TEXT"),
    ("repair ai_conversations.updated_at column",
     "ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    ("repair ai_conversations.archived column",
     "ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE"),
    ("backfill ai_conversations.title default",
     "ALTER TABLE ai_conversations ALTER COLUMN title SET DEFAULT 'New conversation'"),
    ("ai_messages table",
     """
     CREATE TABLE IF NOT EXISTS ai_messages (
         id              SERIAL PRIMARY KEY,
         conversation_id INTEGER     NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
         role            TEXT        NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
         content         TEXT        NOT NULL,
         tokens          INTEGER     DEFAULT 0,
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )
     """),
    ("repair ai_messages.tokens column",
     "ALTER TABLE ai_messages ADD COLUMN IF NOT EXISTS tokens INTEGER DEFAULT 0"),

    # CRITICAL REPAIR: ai_messages.conversation_id was supposed to have
    # REFERENCES ai_conversations(id) ON DELETE CASCADE, but because
    # ai_messages already existed (with a different shape) when this table
    # was first defined, CREATE TABLE IF NOT EXISTS silently skipped that
    # FK along with everything else. Without it, deleting a conversation
    # never cascades to its messages — they become permanently orphaned.
    # Two steps: delete existing orphans (Postgres refuses to add a FK
    # while violating rows exist), then add the FK for real.
    ("delete orphaned ai_messages with no matching conversation",
     "DELETE FROM ai_messages WHERE conversation_id NOT IN (SELECT id FROM ai_conversations)"),
    ("add missing ai_messages -> ai_conversations foreign key",
     """
     DO $$
     BEGIN
         IF NOT EXISTS (
             SELECT 1 FROM pg_constraint WHERE conname = 'ai_messages_conversation_id_fkey'
         ) THEN
             ALTER TABLE ai_messages
                 ADD CONSTRAINT ai_messages_conversation_id_fkey
                 FOREIGN KEY (conversation_id)
                 REFERENCES ai_conversations(id)
                 ON DELETE CASCADE;
         END IF;
     END $$
     """),
    ("index ai_conversations(username)",
     "CREATE INDEX IF NOT EXISTS idx_ai_conversations_username ON ai_conversations(username, updated_at DESC)"),
    ("index ai_messages(conversation_id)",
     "CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id, created_at)"),

    # ── Phase 6: AI Security Copilot — CVE analysis cache ────────────────
    # Same situation: an earlier minimal cve_ai_analysis table already
    # exists in some deployments with only (cve_id, summary, explanation,
    # guidance, attack_scenario, business_impact, analyzed_at, model_used)
    # — missing technical_impact, recommended_actions, description_hash,
    # status, retry_count, error_message, created_at, updated_at. Repaired
    # below.
    ("cve_ai_analysis table",
     """
     CREATE TABLE IF NOT EXISTS cve_ai_analysis (
         cve_id              TEXT        PRIMARY KEY REFERENCES cves(cve_id) ON DELETE CASCADE,
         summary             TEXT,
         explanation         TEXT,
         guidance            TEXT,
         attack_scenario     TEXT,
         business_impact     TEXT,
         technical_impact    TEXT,
         recommended_actions TEXT,
         model_used          TEXT,
         description_hash    TEXT,
         status              TEXT        NOT NULL DEFAULT 'pending',
         retry_count         INTEGER     NOT NULL DEFAULT 0,
         error_message       TEXT,
         analyzed_at         TIMESTAMPTZ,
         created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )
     """),
    ("repair cve_ai_analysis.technical_impact column",
     "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS technical_impact TEXT"),
    ("repair cve_ai_analysis.recommended_actions column",
     "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS recommended_actions TEXT"),
    ("repair cve_ai_analysis.description_hash column",
     "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS description_hash TEXT"),
    ("repair cve_ai_analysis.status column",
     "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'"),
    ("repair cve_ai_analysis.retry_count column",
     "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0"),
    ("repair cve_ai_analysis.error_message column",
     "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS error_message TEXT"),
    ("repair cve_ai_analysis.created_at column",
     "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    ("repair cve_ai_analysis.updated_at column",
     "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    # The status CHECK constraint is added separately (and defensively)
    # because ADD COLUMN with an inline CHECK fails on tables that already
    # have rows violating it; this guards the same way even when there
    # are no rows yet.
    ("add cve_ai_analysis.status CHECK constraint",
     """
     DO $$
     BEGIN
         IF NOT EXISTS (
             SELECT 1 FROM pg_constraint WHERE conname = 'cve_ai_analysis_status_check'
         ) THEN
             ALTER TABLE cve_ai_analysis
                 ADD CONSTRAINT cve_ai_analysis_status_check
                 CHECK (status IN ('pending', 'processing', 'complete', 'failed'));
         END IF;
     END $$
     """),
    ("index cve_ai_analysis(status)",
     "CREATE INDEX IF NOT EXISTS idx_cve_ai_analysis_status ON cve_ai_analysis(status)"),

    # ── Phase 6 Requirement 5: trend analysis ────────────────────────────
    # `matches` only holds current state, so "how does this week compare to
    # last week" had no historical record to answer from at all.
    # risk_snapshots stores one row per day with aggregate counts, written
    # by a daily APScheduler job (see jobs/daily_scan.py).
    ("risk_snapshots table",
     """
     CREATE TABLE IF NOT EXISTS risk_snapshots (
         id                   SERIAL PRIMARY KEY,
         snapshot_date        DATE        NOT NULL UNIQUE,
         total_findings       INTEGER     NOT NULL DEFAULT 0,
         open_findings        INTEGER     NOT NULL DEFAULT 0,
         resolved_findings    INTEGER     NOT NULL DEFAULT 0,
         kev_findings         INTEGER     NOT NULL DEFAULT 0,
         overdue_findings     INTEGER     NOT NULL DEFAULT 0,
         critical_findings    INTEGER     NOT NULL DEFAULT 0,
         high_findings        INTEGER     NOT NULL DEFAULT 0,
         avg_risk_score       NUMERIC,
         max_risk_score       INTEGER,
         total_assets         INTEGER     NOT NULL DEFAULT 0,
         created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )
     """),
    ("index risk_snapshots(snapshot_date)",
     "CREATE INDEX IF NOT EXISTS idx_risk_snapshots_date ON risk_snapshots(snapshot_date DESC)"),

    # ── Phase 6 Requirement 8: chat response cache ───────────────────────
    ("ai_response_cache table",
     """
     CREATE TABLE IF NOT EXISTS ai_response_cache (
         cache_key   TEXT        PRIMARY KEY,
         question    TEXT        NOT NULL,
         response    TEXT        NOT NULL,
         tokens      INTEGER     NOT NULL DEFAULT 0,
         hit_count   INTEGER     NOT NULL DEFAULT 0,
         created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         expires_at  TIMESTAMPTZ NOT NULL
     )
     """),
    ("index ai_response_cache(expires_at)",
     "CREATE INDEX IF NOT EXISTS idx_ai_response_cache_expires ON ai_response_cache(expires_at)"),

    # ── City Exposure Overview feature ───────────────────────────────────
    # Nullable on purpose: existing assets have no city/country data and
    # must keep working unmodified. NULL/blank city is the documented
    # "unassigned asset" case, not an error state.
    ("assets.city column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS city VARCHAR(120)"),
    ("assets.country_code column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS country_code CHAR(2)"),
    ("index assets(country_code, city)",
     "CREATE INDEX IF NOT EXISTS idx_assets_city_country ON assets (country_code, city)"),

    # ── Asset metadata: exposure & network function ──────────────────────
    # exposure defaults to 'Internal' rather than NULL — every asset has a
    # real exposure state whether or not it's been reviewed yet.
    ("assets.exposure column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS exposure TEXT NOT NULL DEFAULT 'Internal'"),
    ("assets.exposure check constraint",
     """
     DO $$
     BEGIN
         IF NOT EXISTS (
             SELECT 1 FROM information_schema.table_constraints
             WHERE table_name = 'assets' AND constraint_name = 'assets_exposure_check'
         ) THEN
             ALTER TABLE assets ADD CONSTRAINT assets_exposure_check
                 CHECK (exposure IN ('Internal', 'External'));
         END IF;
     END $$
     """),
    ("assets.function column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS function TEXT"),
    ("index assets(exposure)",
     "CREATE INDEX IF NOT EXISTS idx_assets_exposure ON assets (exposure)"),
    ("index assets(function)",
     "CREATE INDEX IF NOT EXISTS idx_assets_function ON assets (function)"),

    # ── Patch planning (per-finding) ─────────────────────────────────────
    # Deliberately separate from matches.due_date: due_date is the
    # auto-calculated SLA deadline; planned_patch_date is the analyst's
    # own scheduling decision, never auto-computed or overwritten.
    ("matches.planned_patch_date column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS planned_patch_date DATE"),
    ("matches.patch_notes column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS patch_notes TEXT"),
    ("index matches(planned_patch_date)",
     "CREATE INDEX IF NOT EXISTS idx_matches_planned_patch_date ON matches (planned_patch_date)"),
]


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


def apply_pending_migrations(conn, dry_run: bool, continue_on_error: bool) -> dict:
    """
    Applies every migration in MIGRATIONS whose name is not already
    recorded in schema_version with status='success'. Each migration runs
    in its own transaction. Returns a stats dict.
    """
    stats = {"applied": 0, "skipped": 0, "failed": 0}
    total = len(MIGRATIONS)

    with conn.cursor() as cur:
        cur.execute("SELECT migration_name FROM schema_version WHERE status = 'success'")
        already_applied = {row[0] for row in cur.fetchall()}

    for i, (name, sql) in enumerate(MIGRATIONS, start=1):
        Log.progress(i, total, name[:40])
        if name in already_applied:
            stats["skipped"] += 1
            continue

        if dry_run:
            print()
            Log.info(f"[dry-run] would apply: {name}")
            stats["applied"] += 1
            continue

        start = time.monotonic()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        """
                        INSERT INTO schema_version
                            (version, migration_name, duration_ms, checksum, status)
                        VALUES (%s, %s, %s, %s, 'success')
                        ON CONFLICT (migration_name)
                        DO UPDATE SET status = 'success',
                                      applied_at = NOW(),
                                      duration_ms = EXCLUDED.duration_ms,
                                      checksum = EXCLUDED.checksum
                        """,
                        (
                            CURRENT_SCHEMA_VERSION,
                            name,
                            int((time.monotonic() - start) * 1000),
                            _checksum(sql),
                        ),
                    )
            stats["applied"] += 1
        except Exception as exc:
            conn.rollback()
            stats["failed"] += 1
            print()
            Log.error(f"Migration '{name}' failed: {exc}")
            # Record the failure on its own connection/transaction so a
            # broken transaction state on `conn` doesn't also swallow this.
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO schema_version
                                (version, migration_name, duration_ms, checksum, status)
                            VALUES (%s, %s, %s, %s, 'failed')
                            ON CONFLICT (migration_name)
                            DO UPDATE SET status = 'failed', applied_at = NOW()
                            """,
                            (CURRENT_SCHEMA_VERSION, name,
                             int((time.monotonic() - start) * 1000), _checksum(sql)),
                        )
            except Exception:
                conn.rollback()

            if not continue_on_error:
                raise MigrationError(
                    f"Stopping after migration '{name}' failed. Fix the underlying issue "
                    f"and re-run — already-applied migrations will be skipped automatically. "
                    f"Pass --continue-on-error to keep going instead."
                ) from exc

    print()
    return stats


def seed_required_data(conn) -> None:
    """
    Seed baseline data required for a working install. ARGUS currently has
    no baseline rows to insert — the first user account is created through
    the web UI's registration flow (see dashboard/app.py's /register
    route) rather than pre-seeded here. This function is intentionally a
    documented no-op: it exists so a future release that *does* need
    baseline rows (default roles, default config) has an established,
    idempotent place to add them, using the same
    INSERT ... ON CONFLICT DO NOTHING pattern as everything else in this
    file.
    """
    Log.info("No baseline data to seed (ARGUS users are created via web registration).")


def create_or_refresh_views(conn) -> dict:
    """
    Extract every `CREATE OR REPLACE VIEW ... ;` statement from schema.sql
    and (re-)apply it. CREATE OR REPLACE VIEW is always safe to re-run.

    Anchoring the regex on `VIEW\\s+\\w+\\s+AS` (not just the bare phrase)
    is deliberate: matching the literal text "CREATE OR REPLACE VIEW"
    anywhere would also match it inside an SQL comment that mentions the
    phrase as prose, producing a malformed statement (comment text + the
    real view glued together).
    """
    with open(SCHEMA_FILE, "r", encoding="utf-8") as fh:
        views_sql = fh.read()

    view_statements = re.findall(r"CREATE OR REPLACE VIEW\s+\w+\s+AS[\s\S]+?;", views_sql)
    stats = {"created": 0, "failed": 0}

    for stmt in view_statements:
        match = re.search(r"VIEW\s+(\w+)", stmt)
        label = match.group(1) if match else "unknown"
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(stmt)
            Log.ok(f"view {label}")
            stats["created"] += 1
        except Exception as exc:
            conn.rollback()
            Log.error(f"view {label} failed: {exc}")
            stats["failed"] += 1

    return stats


def create_missing_indexes(conn) -> dict:
    """
    Defensive final pass: every index in REQUIRED_INDEXES should already
    have been created by MIGRATIONS above, but this confirms it and
    creates anything still missing rather than assuming.
    """
    stats = {"created": 0, "already_present": 0}
    index_ddl = {
        "idx_matches_asset_id": "CREATE INDEX IF NOT EXISTS idx_matches_asset_id ON matches(asset_id)",
        "idx_matches_cve_id": "CREATE INDEX IF NOT EXISTS idx_matches_cve_id ON matches(cve_id)",
        "idx_matches_risk": "CREATE INDEX IF NOT EXISTS idx_matches_risk ON matches(risk_score DESC)",
        "idx_matches_status": "CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)",
        "idx_matches_due_date": "CREATE INDEX IF NOT EXISTS idx_matches_due_date ON matches(due_date)",
        "idx_matches_asset_cve": "CREATE INDEX IF NOT EXISTS idx_matches_asset_cve ON matches(asset_id, status)",
        "idx_matches_planned_patch_date": "CREATE INDEX IF NOT EXISTS idx_matches_planned_patch_date ON matches(planned_patch_date)",
        "idx_assets_type": "CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(type)",
        "idx_assets_exposure": "CREATE INDEX IF NOT EXISTS idx_assets_exposure ON assets(exposure)",
        "idx_assets_function": "CREATE INDEX IF NOT EXISTS idx_assets_function ON assets(function)",
        "idx_assets_city_country": "CREATE INDEX IF NOT EXISTS idx_assets_city_country ON assets(country_code, city)",
        "idx_cves_kev": "CREATE INDEX IF NOT EXISTS idx_cves_kev ON cves(kev) WHERE kev = TRUE",
        "idx_cves_cvss": "CREATE INDEX IF NOT EXISTS idx_cves_cvss ON cves(cvss DESC)",
        "idx_ai_conversations_username": "CREATE INDEX IF NOT EXISTS idx_ai_conversations_username ON ai_conversations(username, updated_at DESC)",
        "idx_ai_messages_conversation": "CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id, created_at)",
        "idx_cve_ai_analysis_status": "CREATE INDEX IF NOT EXISTS idx_cve_ai_analysis_status ON cve_ai_analysis(status)",
        "idx_risk_snapshots_date": "CREATE INDEX IF NOT EXISTS idx_risk_snapshots_date ON risk_snapshots(snapshot_date DESC)",
        "idx_ai_response_cache_expires": "CREATE INDEX IF NOT EXISTS idx_ai_response_cache_expires ON ai_response_cache(expires_at)",
    }
    with conn:
        with conn.cursor() as cur:
            for index_name, table in REQUIRED_INDEXES:
                if not table_exists(cur, table):
                    continue  # table itself missing -> not this step's job to report
                if index_exists(cur, index_name):
                    stats["already_present"] += 1
                    continue
                ddl = index_ddl.get(index_name)
                if not ddl:
                    continue
                try:
                    cur.execute(ddl)
                    stats["created"] += 1
                except Exception as exc:
                    Log.error(f"Failed to create index {index_name}: {exc}")
    return stats


# ══════════════════════════════════════════════════════════════════════════
# Verification (steps 12-18)
# ══════════════════════════════════════════════════════════════════════════

def verify_foreign_keys(cur) -> bool:
    all_ok = True
    for table, column in REQUIRED_FOREIGN_KEYS:
        if not table_exists(cur, table):
            Log.check(f"FK {table}.{column}", False, "table missing")
            all_ok = False
            continue
        ok = foreign_key_exists(cur, table, column)
        Log.check(f"FK {table}.{column}", ok)
        all_ok = all_ok and ok
    return all_ok


def verify_constraints(cur) -> bool:
    all_ok = True
    for table, column in REQUIRED_CHECK_CONSTRAINTS:
        if not table_exists(cur, table):
            Log.check(f"CHECK {table}.{column}", False, "table missing")
            all_ok = False
            continue
        ok = check_constraint_exists(cur, table, column)
        Log.check(f"CHECK {table}.{column}", ok)
        all_ok = all_ok and ok
    for table, columns in REQUIRED_UNIQUE_CONSTRAINTS:
        if not table_exists(cur, table):
            Log.check(f"UNIQUE {table}{columns}", False, "table missing")
            all_ok = False
            continue
        ok = unique_constraint_exists(cur, table, columns)
        Log.check(f"UNIQUE {table}{columns}", ok)
        all_ok = all_ok and ok
    return all_ok


def verify_triggers(cur) -> None:
    # ARGUS's actual schema defines no triggers today. This step exists so
    # the day a migration adds one, verification is already wired up —
    # nothing here to fail on an install that matches the real schema.
    Log.info("ARGUS defines no database triggers today — nothing to verify.")


def verify_functions(cur) -> None:
    # Same story as triggers: no CREATE FUNCTION anywhere in schema.sql or
    # migrate.py today.
    Log.info("ARGUS defines no database functions today — nothing to verify.")


def verify_required_tables(cur) -> dict:
    results = {}
    for table in REQUIRED_TABLES:
        results[table] = table_exists(cur, table)
        Log.check(table, results[table])
    return results


def verify_required_views(cur) -> dict:
    results = {}
    for view in REQUIRED_VIEWS:
        results[view] = view_exists(cur, view)
        Log.check(view, results[view])
    return results


def verify_indexes(cur) -> dict:
    results = {}
    for index_name, table in REQUIRED_INDEXES:
        if not table_exists(cur, table):
            results[index_name] = False
            continue
        results[index_name] = index_exists(cur, index_name)
    return results


def get_database_version(cur) -> dict:
    cur.execute(
        "SELECT COUNT(*) FROM schema_version WHERE status = 'success'"
    )
    applied_count = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM schema_version WHERE status = 'failed'"
    )
    failed_count = cur.fetchone()[0]
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "migrations_applied": applied_count,
        "migrations_failed": failed_count,
        "migrations_total": len(MIGRATIONS),
    }


def verify_database(conn) -> bool:
    """Runs the full verification checklist (steps 12-18). Returns overall pass/fail."""
    with conn.cursor() as cur:
        Log.step(12, 19, "Verifying foreign keys")
        fk_ok = verify_foreign_keys(cur)

        Log.step(13, 19, "Verifying constraints")
        constraints_ok = verify_constraints(cur)

        Log.step(14, 19, "Verifying triggers")
        verify_triggers(cur)

        Log.step(15, 19, "Verifying functions")
        verify_functions(cur)

        Log.step(16, 19, "Verifying required tables")
        tables = verify_required_tables(cur)

        Log.step(17, 19, "Verifying required views")
        views = verify_required_views(cur)

        Log.step(18, 19, "Checking database version")
        version_info = get_database_version(cur)
        Log.info(f"Schema version: {version_info['schema_version']}")
        Log.info(
            f"Migrations applied: {version_info['migrations_applied']}/"
            f"{version_info['migrations_total']}"
            + (f"  ({version_info['migrations_failed']} failed)"
               if version_info["migrations_failed"] else "")
        )

    return (
        fk_ok
        and constraints_ok
        and all(tables.values())
        and all(views.values())
        and version_info["migrations_failed"] == 0
    )


def print_summary(stats: dict, elapsed: float) -> None:
    print("\n" + "=" * 72)
    print("  ARGUS DATABASE MIGRATION SUMMARY")
    print("=" * 72)
    print(f"  Database Version     : {CURRENT_SCHEMA_VERSION}")
    print(f"  Tables Verified       : {stats.get('tables_verified', 0)}")
    print(f"  Migrations Applied    : {stats.get('migrations_applied', 0)}")
    print(f"  Migrations Skipped    : {stats.get('migrations_skipped', 0)} (already applied)")
    if stats.get("migrations_failed"):
        print(f"  Migrations Failed     : {stats['migrations_failed']}")
    print(f"  Views Created/Refreshed: {stats.get('views_created', 0)}")
    print(f"  Indexes Created       : {stats.get('indexes_created', 0)}")
    print(f"  Indexes Already Present: {stats.get('indexes_present', 0)}")
    print(f"  Migration Time         : {elapsed:.2f}s")
    print("=" * 72)
    if stats.get("overall_ok"):
        print("\n  Database successfully initialized. Ready to launch ARGUS.\n")
    else:
        print(
            "\n  Database migration completed with problems — see [ERROR] lines "
            "above before launching ARGUS.\n"
        )


# ══════════════════════════════════════════════════════════════════════════
# Main flow
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="ARGUS database migration system")
    parser.add_argument("--verify-only", action="store_true",
                         help="Only run the verification checklist against the current database.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Show what would be applied without applying it.")
    parser.add_argument("--continue-on-error", action="store_true",
                         help="Keep applying later migrations after one fails.")
    parser.add_argument("-y", "--yes", action="store_true",
                         help="Don't prompt before creating the database.")
    args = parser.parse_args()

    start_time = time.monotonic()
    stats = {}

    print("ARGUS Database Migration\n")
    print(f"  Target: {DB_CONFIG['user']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}\n")

    total_steps = 19

    # Step 1: verify connectivity to the PostgreSQL server itself.
    Log.step(1, total_steps, "Verifying PostgreSQL connection")
    try:
        maint_conn = get_maintenance_connection()
    except MigrationError as exc:
        Log.error(str(exc))
        return 1
    Log.ok(f"Connected to PostgreSQL server at {DB_CONFIG['host']}:{DB_CONFIG['port']}.")

    # Step 2: does the target database exist? Create it if not (and we can).
    Log.step(2, total_steps, f"Checking whether database '{DB_CONFIG['database']}' exists")
    if not args.verify_only:
        if not create_database_if_missing(maint_conn, DB_CONFIG["database"], args.yes):
            maint_conn.close()
            return 1
    else:
        if not database_exists(maint_conn, DB_CONFIG["database"]):
            Log.error(f"Database '{DB_CONFIG['database']}' does not exist. Run without --verify-only first.")
            maint_conn.close()
            return 1
        Log.ok(f"Database '{DB_CONFIG['database']}' exists.")
    maint_conn.close()

    # Step 3: connect to the target database.
    Log.step(3, total_steps, f"Connecting to '{DB_CONFIG['database']}'")
    try:
        conn = _raw_connect(DB_CONFIG["database"])
    except psycopg2.OperationalError as exc:
        Log.error(classify_connection_error(exc))
        return 1
    Log.ok("Connected.")

    try:
        # Step 4: schema_version bookkeeping table.
        Log.step(4, total_steps, "Ensuring schema_version table exists")
        if not args.dry_run:
            ensure_schema_version_table(conn)
        Log.ok("schema_version ready.")

        # Step 5: check core tables.
        Log.step(5, total_steps, "Checking core tables")
        with conn.cursor() as cur:
            missing_core = verify_schema(cur, CORE_TABLES)
        if missing_core:
            Log.warn(f"Missing core tables: {', '.join(missing_core)}")
        else:
            Log.ok("All core tables present.")

        # Step 6: execute schema.sql automatically if anything core is missing.
        Log.step(6, total_steps, "Applying schema.sql if needed")
        if missing_core and not args.verify_only:
            if args.dry_run:
                Log.info(f"[dry-run] would execute schema.sql to create: {', '.join(missing_core)}")
            else:
                execute_schema(conn)
        elif missing_core and args.verify_only:
            Log.error("Core tables missing and --verify-only was passed; nothing will be created.")
        else:
            Log.ok("schema.sql not needed — core tables already exist.")

        # Step 7: verify schema.sql actually worked.
        Log.step(7, total_steps, "Verifying core schema")
        with conn.cursor() as cur:
            still_missing = verify_schema(cur, CORE_TABLES)
        if still_missing and not args.dry_run:
            raise MigrationError(
                f"Core tables still missing after schema.sql: {', '.join(still_missing)}. "
                f"Check the [ERROR] output above from step 6."
            )
        Log.ok("Core schema verified.") if not still_missing else Log.warn("Skipped (dry-run).")

        # Step 8: incremental migrations.
        Log.step(8, total_steps, "Applying incremental migrations")
        if not args.verify_only:
            mig_stats = apply_pending_migrations(conn, args.dry_run, args.continue_on_error)
        else:
            mig_stats = {"applied": 0, "skipped": 0, "failed": 0}
            Log.info("Skipped (--verify-only).")
        stats["migrations_applied"] = mig_stats["applied"]
        stats["migrations_skipped"] = mig_stats["skipped"]
        stats["migrations_failed"] = mig_stats["failed"]

        # Step 9: seed baseline data.
        Log.step(9, total_steps, "Seeding required data")
        if not args.verify_only and not args.dry_run:
            seed_required_data(conn)
        else:
            Log.info("Skipped.")

        # Step 10: create/refresh views.
        Log.step(10, total_steps, "Creating/refreshing database views")
        if not args.verify_only and not args.dry_run:
            view_stats = create_or_refresh_views(conn)
        else:
            view_stats = {"created": 0, "failed": 0}
            Log.info("Skipped.")
        stats["views_created"] = view_stats["created"]

        # Step 11: create any indexes still missing.
        Log.step(11, total_steps, "Creating indexes")
        if not args.verify_only and not args.dry_run:
            index_stats = create_missing_indexes(conn)
        else:
            index_stats = {"created": 0, "already_present": 0}
            Log.info("Skipped.")
        stats["indexes_created"] = index_stats["created"]
        stats["indexes_present"] = index_stats["already_present"]

        # Steps 12-18: verification.
        overall_ok = verify_database(conn)
        stats["overall_ok"] = overall_ok
        with conn.cursor() as cur:
            stats["tables_verified"] = sum(1 for t in REQUIRED_TABLES if table_exists(cur, t))

        # Step 19: summary.
        Log.step(19, total_steps, "Final summary")
        print_summary(stats, time.monotonic() - start_time)

        # Step 9.5 (post-migration hook): queue any CVE that predates the
        # AI analysis pipeline. Not one of the 19 numbered steps because
        # it depends on database/cve_analysis.py, which itself depends on
        # the connection pool in database/db.py — only safe to import once
        # we're certain the database and its tables exist.
        if not args.verify_only and not args.dry_run:
            try:
                sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
                from database.cve_analysis import backfill_missing_analysis
                count = backfill_missing_analysis()
                Log.info(f"Queued {count} CVE(s) that had no AI analysis row yet.")
            except Exception as exc:
                Log.warn(f"AI analysis backfill skipped: {exc}")

        return 0 if overall_ok else 1

    except MigrationError as exc:
        Log.error(str(exc))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
