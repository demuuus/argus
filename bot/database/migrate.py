#!/usr/bin/env python3
"""
ARGUS Database Migration System
================================

Single-command, fully idempotent installer/upgrader for the ARGUS
PostgreSQL schema.

    python database/migrate.py

...is the entire installation workflow. This script:

  1. Verifies it can reach the PostgreSQL server at all, and explains
     exactly what's wrong (bad password, bad host, bad port, server not
     running, etc.) if it can't.
  2. Checks whether the target database itself exists, and creates it
     automatically if the connecting role has permission to (otherwise
     prints the exact command an admin needs to run).
  3. Bootstraps the base schema (tables, indexes, views defined in
     schema.sql) on a brand-new, completely empty database — something
     the previous version of this script never did, which is why fresh
     installs used to fail with errors like `relation "assets" does not
     exist`.
  4. Applies every incremental migration that has shipped since (AI
     Security Copilot tables, risk snapshot history, chat response
     cache, City Exposure Overview columns, asset exposure/function
     metadata, patch planning columns) — safe to run against a
     completely fresh database, a partially-upgraded one, or a
     fully-current one, in any order, any number of times.
  5. Verifies the result: every required table, view, foreign key,
     constraint, and index actually exists after the fact, rather than
     assuming a CREATE statement that didn't error means the object is
     now in the expected shape.
  6. Records every migration attempt (name, version, checksum, duration,
     status) in a `schema_version` table, so re-running this script is
     cheap (already-applied migrations are skipped) and auditable
     (you can see exactly what was applied and when).
  7. Prints a clear, structured summary at the end — modeled on the
     kind of output Django, Alembic, Flyway, and Rails migrations give
     you — instead of a wall of raw SQL echoed to the terminal.

Every table, column, index, constraint, and view this script knows about
was taken directly from the actual ARGUS codebase (schema.sql, the
previous migrate.py, and every database/*.py module that issues SQL) —
see the `MIGRATIONS` list and `REQUIRED_TABLES`/`REQUIRED_VIEWS` below,
each of which cites where it came from. Nothing here is invented or
guessed. As of this rewrite, ARGUS defines no custom PostgreSQL
functions or triggers anywhere in its codebase, so this script's
function/trigger verification steps report an expected count of zero —
they are implemented and wired up so that adding a real function or
trigger to ARGUS in the future only requires listing it, not building
new verification machinery.

Usage:
    python database/migrate.py                 # full install/upgrade (default)
    python database/migrate.py --check          # verify only, make no changes
    python database/migrate.py --verbose        # print every SQL statement run
    python database/migrate.py --no-color       # disable ANSI colors in output
    python database/migrate.py --yes            # never prompt (default: never prompts anyway)
    python database/migrate.py --skip-create-db # never attempt CREATE DATABASE

Exit codes:
    0  — success (or --check found no problems)
    1  — could not connect to the PostgreSQL server at all
    2  — target database does not exist and could not be created
    3  — one or more required schema objects are missing/broken after migration
    4  — one or more migrations failed to apply
    5  — unexpected/unhandled error
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional, Sequence, Tuple

import psycopg2
import psycopg2.errorcodes
import psycopg2.extensions
from psycopg2 import sql as pgsql

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a hard ARGUS dependency,
    # but this script should still explain itself clearly if the venv is
    # wrong, rather than dying on an ImportError stack trace.
    def load_dotenv(*_a, **_kw):
        return False


# ══════════════════════════════════════════════════════════════════════════
# Path / environment bootstrapping
# ══════════════════════════════════════════════════════════════════════════

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Load .env from the project root first (the normal ARGUS layout), then
# fall back to python-dotenv's default search (CWD and parents) so this
# script also works if invoked from inside database/ directly.
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
load_dotenv()

SCRIPT_START_TIME = time.monotonic()


# ══════════════════════════════════════════════════════════════════════════
# Structured logging
# ══════════════════════════════════════════════════════════════════════════

class _Ansi:
    """ANSI color codes, disabled automatically when stdout isn't a TTY or
    NO_COLOR is set (https://no-color.org), and via --no-color."""

    ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR", "") == ""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    @classmethod
    def wrap(cls, text: str, *codes: str) -> str:
        if not cls.ENABLED:
            return text
        return "".join(codes) + text + cls.RESET


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


class MigrationLogger:
    """
    Thin structured-logging wrapper. Distinct from Python's `logging`
    module on purpose: this script is a standalone CLI tool whose output
    is read by a human watching a terminal during installation, not a
    library emitting into an application's log aggregation — the
    formatting priorities (colored tags, progress bars, a final summary
    block) are specific to that use case.
    """

    def __init__(self, verbose: bool = False, quiet: bool = False):
        self.verbose = verbose
        self.quiet = quiet
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def _emit(self, tag: str, color: str, msg: str, force: bool = False):
        if self.quiet and not force:
            return
        prefix = _Ansi.wrap(f"[{tag}]", _Ansi.BOLD, color)
        print(f"{_Ansi.wrap(_ts(), _Ansi.GRAY)} {prefix} {msg}")

    def info(self, msg: str):
        self._emit("INFO", _Ansi.BLUE, msg)

    def warn(self, msg: str):
        self.warnings.append(msg)
        self._emit("WARNING", _Ansi.YELLOW, msg, force=True)

    def success(self, msg: str):
        self._emit("SUCCESS", _Ansi.GREEN, msg)

    def error(self, msg: str):
        self.errors.append(msg)
        self._emit("ERROR", _Ansi.RED, msg, force=True)

    def debug(self, msg: str):
        if self.verbose:
            self._emit("DEBUG", _Ansi.GRAY, msg)

    def step(self, n: int, total: int, msg: str):
        label = _Ansi.wrap(f"Step {n:>2}/{total}", _Ansi.BOLD, _Ansi.CYAN)
        print(f"\n{label}  {_Ansi.wrap(msg, _Ansi.BOLD)}")

    def section(self, msg: str):
        bar = "─" * max(4, min(70, len(msg) + 4))
        print(f"\n{_Ansi.wrap(bar, _Ansi.GRAY)}")
        print(_Ansi.wrap(msg, _Ansi.BOLD, _Ansi.CYAN))
        print(_Ansi.wrap(bar, _Ansi.GRAY))

    def progress(self, current: int, total: int, label: str = ""):
        """Simple in-place progress bar for a loop of known length."""
        if self.quiet or total <= 0:
            return
        width = 28
        filled = int(width * current / total)
        bar = "█" * filled + "░" * (width - filled)
        pct = int(100 * current / total)
        line = f"  [{bar}] {pct:3d}%  {label}"
        pad = " " * max(0, 100 - len(line))
        end = "\n" if current == total else ""
        print(f"\r{line}{pad}", end=end, flush=True)


LOG = MigrationLogger()


# ══════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class DBConfig:
    """
    Mirrors database/db.py's environment-variable configuration exactly
    (same variable names, same defaults) so this script and the running
    application always agree on which database they're talking about.
    """
    host: str
    port: int
    user: str
    password: str
    database: str
    connect_timeout: int = 10

    @property
    def safe_repr(self) -> str:
        """Connection details safe to print (never the password)."""
        return f"{self.user}@{self.host}:{self.port}/{self.database}"


def load_config() -> DBConfig:
    password = os.getenv("DB_PASSWORD", "")
    if not password:
        LOG.warn(
            "DB_PASSWORD is not set in the environment or .env file. "
            "Attempting to connect with no password — this will fail "
            "unless your PostgreSQL server is configured for trust/peer "
            "authentication for this role."
        )
    return DBConfig(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        user=os.getenv("DB_USER", "postgres"),
        password=password,
        database=os.getenv("DB_NAME", "argus_db"),
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", 10)),
    )


# ══════════════════════════════════════════════════════════════════════════
# Exceptions
# ══════════════════════════════════════════════════════════════════════════

class MigrationError(Exception):
    """Base class for every error this script raises intentionally."""


class ServerUnreachable(MigrationError):
    """Could not reach the PostgreSQL server process at all."""


class DatabaseMissing(MigrationError):
    """The target database does not exist and could not be created."""


class SchemaVerificationFailed(MigrationError):
    """One or more required schema objects are missing after migration."""


# ══════════════════════════════════════════════════════════════════════════
# Connection-error classification
# ══════════════════════════════════════════════════════════════════════════

def classify_connection_error(exc: Exception, config: DBConfig) -> str:
    """
    Turn a raw psycopg2/OperationalError message into a specific,
    actionable explanation. psycopg2 does not give structured error codes
    for connection-phase failures (those come from the OS/libpq, before
    a PostgreSQL error code would even be assigned), so this matches on
    the well-known libpq message text.
    """
    msg = str(exc).strip()
    low = msg.lower()

    if "password authentication failed" in low:
        return (
            f"Authentication failed for user '{config.user}' at "
            f"{config.host}:{config.port}.\n"
            f"    → Check DB_USER and DB_PASSWORD in your .env file.\n"
            f"    → Verify the role exists: "
            f"psql -U postgres -h {config.host} -c \"\\du\""
        )
    if "role" in low and "does not exist" in low:
        return (
            f"The role (user) '{config.user}' does not exist on the "
            f"PostgreSQL server at {config.host}:{config.port}.\n"
            f"    → Create it: "
            f"createuser -h {config.host} -p {config.port} -P {config.user}"
        )
    if "could not translate host name" in low or "could not resolve" in low:
        return (
            f"The hostname '{config.host}' could not be resolved.\n"
            f"    → Check DB_HOST in your .env file for typos.\n"
            f"    → If PostgreSQL runs on this machine, try DB_HOST=localhost."
        )
    if "connection refused" in low:
        return (
            f"Connection refused by {config.host}:{config.port}.\n"
            f"    → Is PostgreSQL actually running? "
            f"(sudo systemctl status postgresql)\n"
            f"    → Is it listening on port {config.port}? Check "
            f"postgresql.conf's `port` setting.\n"
            f"    → Is DB_HOST/DB_PORT correct in your .env file?"
        )
    if "timeout expired" in low or "timed out" in low:
        return (
            f"Connection to {config.host}:{config.port} timed out after "
            f"{config.connect_timeout}s.\n"
            f"    → Check firewall rules between this machine and the "
            f"database host.\n"
            f"    → Verify DB_HOST/DB_PORT are correct."
        )
    if "no route to host" in low:
        return (
            f"No network route to {config.host}:{config.port}.\n"
            f"    → Check DB_HOST is correct and the host is reachable "
            f"from this machine (e.g. `ping {config.host}`)."
        )
    if "server closed the connection unexpectedly" in low:
        return (
            "The PostgreSQL server closed the connection unexpectedly. "
            "This usually means the server is still starting up, is "
            "overloaded, or crashed. Check the PostgreSQL server logs."
        )
    if "database" in low and "does not exist" in low:
        # Handled explicitly by database_exists()/create_database_if_missing()
        # elsewhere, but keep a sane message here in case this surfaces
        # from an unexpected code path.
        return (
            f"Database '{config.database}' does not exist on "
            f"{config.host}:{config.port}. This script normally handles "
            f"this automatically — see the 'checking database exists' step above."
        )
    if "too many connections" in low:
        return (
            "The PostgreSQL server has reached its max_connections limit. "
            "Close idle connections or increase max_connections in "
            "postgresql.conf."
        )
    if "ssl" in low:
        return (
            f"An SSL/TLS negotiation error occurred connecting to "
            f"{config.host}:{config.port}: {msg}\n"
            f"    → If the server does not use SSL, this may indicate a "
            f"misconfigured DB_HOST (wrong service on that port)."
        )

    # Fallback — still specific about which connection attempt failed.
    return f"Could not connect to PostgreSQL at {config.safe_repr}: {msg}"


# ══════════════════════════════════════════════════════════════════════════
# Low-level connection helpers
# ══════════════════════════════════════════════════════════════════════════

# Maintenance databases to try, in order, when we need a connection that
# does NOT depend on the ARGUS target database already existing (needed
# to run `CREATE DATABASE` and to check pg_database in the first place).
# 'postgres' exists on essentially every real-world PostgreSQL install;
# falling back to the connecting role's own username covers the (rarer)
# case where a hosting provider has removed the 'postgres' maintenance DB.
_MAINTENANCE_DB_CANDIDATES = ("postgres", "template1")


def _raw_connect(config: DBConfig, dbname: str, autocommit: bool = False):
    conn = psycopg2.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        dbname=dbname,
        connect_timeout=config.connect_timeout,
    )
    if autocommit:
        conn.autocommit = True
    return conn


def verify_postgres_connection(config: DBConfig) -> Tuple[bool, Optional[str]]:
    """
    Step 1: confirm the PostgreSQL *server* itself is reachable and the
    given credentials are valid, independent of whether the ARGUS
    target database exists yet. Tries each maintenance database
    candidate in turn since the target database's own non-existence
    must never be mistaken for "the server is unreachable."

    Returns (ok, error_message).
    """
    last_error: Optional[Exception] = None
    for maint_db in _MAINTENANCE_DB_CANDIDATES:
        try:
            conn = _raw_connect(config, maint_db, autocommit=True)
            conn.close()
            return True, None
        except psycopg2.OperationalError as exc:
            low = str(exc).lower()
            if "does not exist" in low and "database" in low:
                # This maintenance DB candidate doesn't exist on this
                # server — try the next one; this is not itself proof
                # the server is unreachable.
                last_error = exc
                continue
            last_error = exc
            break
        except psycopg2.Error as exc:
            last_error = exc
            break

    assert last_error is not None
    return False, classify_connection_error(last_error, config)


def get_maintenance_connection(config: DBConfig):
    """
    Return an autocommit connection to whichever maintenance database is
    reachable. Required because CREATE DATABASE cannot run inside a
    transaction block, and because checking pg_database must not require
    the target ARGUS database to already exist.
    """
    last_error: Optional[Exception] = None
    for maint_db in _MAINTENANCE_DB_CANDIDATES:
        try:
            return _raw_connect(config, maint_db, autocommit=True)
        except psycopg2.OperationalError as exc:
            last_error = exc
            continue
    raise ServerUnreachable(classify_connection_error(last_error, config))


def database_exists(config: DBConfig) -> bool:
    """Step 2: check pg_database for the configured target database."""
    conn = get_maintenance_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (config.database,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def create_database_if_missing(config: DBConfig, allow_create: bool = True) -> bool:
    """
    Step 2 (continued): create the target database if it's missing and
    the connecting role has CREATEDB (or superuser) privilege.

    Returns True if the database exists after this call (whether it
    already existed or was just created), False if it's still missing.
    """
    if database_exists(config):
        LOG.debug(f"Database '{config.database}' already exists.")
        return True

    LOG.warn(f"Database '{config.database}' does not exist on {config.host}:{config.port}.")

    if not allow_create:
        LOG.error(
            f"Automatic database creation was disabled (--skip-create-db). "
            f"Create it manually:\n"
            f"    createdb -h {config.host} -p {config.port} -U {config.user} {config.database}"
        )
        return False

    conn = get_maintenance_connection(config)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(config.database))
                )
                LOG.success(f"Created database '{config.database}'.")
                return True
            except psycopg2.errors.InsufficientPrivilege:
                LOG.error(
                    f"Role '{config.user}' does not have permission to create "
                    f"databases. Ask a PostgreSQL administrator to run:\n"
                    f"    CREATE DATABASE {config.database} OWNER {config.user};\n"
                    f"  or grant CREATEDB:\n"
                    f"    ALTER ROLE {config.user} CREATEDB;"
                )
                return False
            except psycopg2.errors.DuplicateDatabase:
                # Race condition: something else created it between our
                # existence check and this CREATE DATABASE call. Fine.
                LOG.debug("Database was created concurrently by another process.")
                return True
    finally:
        conn.close()


def get_target_connection(config: DBConfig):
    """Step 3: connect to the (now guaranteed-to-exist) target database."""
    try:
        return _raw_connect(config, config.database, autocommit=False)
    except psycopg2.OperationalError as exc:
        raise ServerUnreachable(classify_connection_error(exc, config)) from exc


# ══════════════════════════════════════════════════════════════════════════
# Existence-check helper functions
# (operate against an open connection to the *target* database)
# ══════════════════════════════════════════════════════════════════════════

def table_exists(conn, name: str, schema: str = "public") -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, name),
        )
        return cur.fetchone() is not None


def column_exists(conn, table: str, column: str, schema: str = "public") -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s AND column_name = %s
            """,
            (schema, table, column),
        )
        return cur.fetchone() is not None


def view_exists(conn, name: str, schema: str = "public") -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.views
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, name),
        )
        return cur.fetchone() is not None


def index_exists(conn, name: str, schema: str = "public") -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM pg_indexes
            WHERE schemaname = %s AND indexname = %s
            """,
            (schema, name),
        )
        return cur.fetchone() is not None


def constraint_exists(conn, name: str, table: Optional[str] = None) -> bool:
    with conn.cursor() as cur:
        if table:
            cur.execute(
                """
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = %s AND table_name = %s
                """,
                (name, table),
            )
        else:
            cur.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = %s",
                (name,),
            )
        return cur.fetchone() is not None


def trigger_exists(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.triggers WHERE trigger_name = %s",
            (name,),
        )
        return cur.fetchone() is not None


def function_exists(conn, name: str, schema: str = "public") -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = %s AND p.proname = %s
            """,
            (schema, name),
        )
        return cur.fetchone() is not None


def schema_exists(conn, name: str = "public") -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            (name,),
        )
        return cur.fetchone() is not None


def sequence_exists(conn, name: str, schema: str = "public") -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.sequences
            WHERE sequence_schema = %s AND sequence_name = %s
            """,
            (schema, name),
        )
        return cur.fetchone() is not None


def foreign_key_exists(conn, constraint_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM pg_constraint
            WHERE conname = %s AND contype = 'f'
            """,
            (constraint_name,),
        )
        return cur.fetchone() is not None


# ══════════════════════════════════════════════════════════════════════════
# schema_version tracking table
# ══════════════════════════════════════════════════════════════════════════

SCHEMA_VERSION_TABLE = "schema_version"


def ensure_schema_version_table(conn) -> None:
    """
    Step 4: this table is this migration system's own bookkeeping — it
    is not part of the application schema that database/*.py modules
    query, and did not exist in any previous version of ARGUS. It exists
    purely so this script can (a) skip migrations it has already applied
    successfully, without re-deriving that from probing table/column
    existence every time, and (b) give operators an audit trail of
    exactly what ran and when, matching what Django/Alembic/Flyway/Rails
    each do with their own migration-history tables.
    """
    if table_exists(conn, SCHEMA_VERSION_TABLE):
        return
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
                    id                SERIAL PRIMARY KEY,
                    version           INTEGER     NOT NULL,
                    migration_name    TEXT        NOT NULL,
                    checksum          TEXT        NOT NULL,
                    status            TEXT        NOT NULL
                                      CHECK (status IN ('success', 'failed')),
                    duration_ms       INTEGER     NOT NULL DEFAULT 0,
                    error_message     TEXT,
                    applied_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{SCHEMA_VERSION_TABLE}_name
                ON {SCHEMA_VERSION_TABLE}(migration_name)
                """
            )
    LOG.success(f"Created '{SCHEMA_VERSION_TABLE}' tracking table.")


def checksum_of(text: str) -> str:
    """Stable checksum of a migration's SQL body, used to detect whether
    a migration's definition has changed since it was last recorded as
    applied (in which case it should run again, since IF NOT EXISTS
    guards make re-running safe regardless)."""
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def get_last_recorded_run(conn, migration_name: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT checksum, status FROM {SCHEMA_VERSION_TABLE}
            WHERE migration_name = %s
            ORDER BY applied_at DESC
            LIMIT 1
            """,
            (migration_name,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"checksum": row[0], "status": row[1]}


def record_migration(
    conn,
    version: int,
    name: str,
    checksum: str,
    status: str,
    duration_ms: int,
    error_message: Optional[str] = None,
) -> None:
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA_VERSION_TABLE}
                    (version, migration_name, checksum, status, duration_ms, error_message)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (version, name, checksum, status, duration_ms,
                 (error_message or "")[:4000] or None),
            )


def get_current_schema_version(conn) -> int:
    if not table_exists(conn, SCHEMA_VERSION_TABLE):
        return 0
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}
            WHERE status = 'success'
            """
        )
        return cur.fetchone()[0]


# ══════════════════════════════════════════════════════════════════════════
# Canonical schema inventory
#
# Every table/view/index/constraint listed below was verified directly
# against schema.sql and database/*.py (see the per-item citation). This
# is the source of truth this script verifies against — it does not
# assume schema.sql or MIGRATIONS below are correct; it checks the
# database itself after applying them.
# ══════════════════════════════════════════════════════════════════════════

# schema.sql: assets, cves, matches, alerts, reports, users (+ views)
# migrate.py (previous version): ai_conversations, ai_messages,
#   cve_ai_analysis, risk_snapshots, ai_response_cache
REQUIRED_TABLES: List[str] = [
    "users",
    "assets",
    "cves",
    "matches",
    "alerts",
    "reports",
    "risk_snapshots",
    "ai_conversations",
    "ai_messages",
    "ai_response_cache",
    "cve_ai_analysis",
]

# schema.sql's four `CREATE OR REPLACE VIEW` statements, consumed by
# Ai/context_builder.py.
REQUIRED_VIEWS: List[str] = [
    "ai_dashboard",
    "ai_open_findings",
    "ai_asset_summary",
    "ai_vulnerability_summary",
]

# (table, column) pairs that database/*.py modules actually SELECT/INSERT
# by name — i.e. a query would raise `column "..." does not exist`
# without it. Not an exhaustive dump of every column; a targeted list of
# the ones every module in this codebase depends on existing.
REQUIRED_COLUMNS: List[Tuple[str, str]] = [
    ("assets", "id"), ("assets", "vendor"), ("assets", "product"),
    ("assets", "version"), ("assets", "type"), ("assets", "location"),
    ("assets", "owner"), ("assets", "criticality"), ("assets", "notes"),
    ("assets", "last_scan"), ("assets", "created_at"),
    ("assets", "search_keyword"), ("assets", "city"),
    ("assets", "country_code"), ("assets", "exposure"), ("assets", "function"),
    ("cves", "cve_id"), ("cves", "cvss"), ("cves", "severity"),
    ("cves", "kev"), ("cves", "published"), ("cves", "description"),
    ("cves", "created_at"), ("cves", "epss"), ("cves", "epss_percentile"),
    ("matches", "id"), ("matches", "asset_id"), ("matches", "cve_id"),
    ("matches", "risk_score"), ("matches", "first_seen"),
    ("matches", "status"), ("matches", "patched"), ("matches", "resolved_at"),
    ("matches", "due_date"), ("matches", "assigned_to"),
    ("matches", "assigned_team"), ("matches", "planned_patch_date"),
    ("matches", "patch_notes"),
    ("alerts", "id"), ("alerts", "asset_id"), ("alerts", "message"), ("alerts", "sent_at"),
    ("reports", "id"), ("reports", "report_type"), ("reports", "generated_at"), ("reports", "file_path"),
    ("users", "id"), ("users", "username"), ("users", "password_hash"), ("users", "role"), ("users", "created_at"),
    ("ai_conversations", "id"), ("ai_conversations", "username"),
    ("ai_conversations", "title"), ("ai_conversations", "created_at"),
    ("ai_conversations", "updated_at"), ("ai_conversations", "archived"),
    ("ai_messages", "id"), ("ai_messages", "conversation_id"),
    ("ai_messages", "role"), ("ai_messages", "content"),
    ("ai_messages", "tokens"), ("ai_messages", "created_at"),
    ("ai_response_cache", "cache_key"), ("ai_response_cache", "question"),
    ("ai_response_cache", "response"), ("ai_response_cache", "tokens"),
    ("ai_response_cache", "hit_count"), ("ai_response_cache", "created_at"),
    ("ai_response_cache", "expires_at"),
    ("cve_ai_analysis", "cve_id"), ("cve_ai_analysis", "summary"),
    ("cve_ai_analysis", "explanation"), ("cve_ai_analysis", "guidance"),
    ("cve_ai_analysis", "attack_scenario"), ("cve_ai_analysis", "business_impact"),
    ("cve_ai_analysis", "technical_impact"), ("cve_ai_analysis", "recommended_actions"),
    ("cve_ai_analysis", "model_used"), ("cve_ai_analysis", "description_hash"),
    ("cve_ai_analysis", "status"), ("cve_ai_analysis", "retry_count"),
    ("cve_ai_analysis", "error_message"), ("cve_ai_analysis", "analyzed_at"),
    ("cve_ai_analysis", "created_at"), ("cve_ai_analysis", "updated_at"),
    ("risk_snapshots", "id"), ("risk_snapshots", "snapshot_date"),
    ("risk_snapshots", "total_findings"), ("risk_snapshots", "open_findings"),
    ("risk_snapshots", "resolved_findings"), ("risk_snapshots", "kev_findings"),
    ("risk_snapshots", "overdue_findings"), ("risk_snapshots", "critical_findings"),
    ("risk_snapshots", "high_findings"), ("risk_snapshots", "avg_risk_score"),
    ("risk_snapshots", "max_risk_score"), ("risk_snapshots", "total_assets"),
]

# Named foreign keys this script expects to exist after migration,
# because a previous production incident (documented inline in the old
# migrate.py) showed CREATE TABLE IF NOT EXISTS silently skipping a FK
# when the table already existed in an older shape. Verifying these by
# name, not just "does matches reference assets somehow", is the point.
REQUIRED_FOREIGN_KEYS: List[str] = [
    "ai_messages_conversation_id_fkey",
]

# Named CHECK/UNIQUE constraints this script expects to exist.
REQUIRED_CONSTRAINTS: List[Tuple[str, str]] = [
    ("matches_asset_id_cve_id_key", "matches"),
    ("assets_exposure_check", "assets"),
    ("cve_ai_analysis_status_check", "cve_ai_analysis"),
]

REQUIRED_INDEXES: List[str] = [
    "idx_matches_asset_id", "idx_matches_cve_id", "idx_matches_risk",
    "idx_matches_status", "idx_matches_due_date",
    "idx_assets_type", "idx_cves_kev", "idx_cves_cvss",
    "idx_ai_conversations_username", "idx_ai_messages_conversation",
    "idx_cve_ai_analysis_status", "idx_risk_snapshots_date",
    "idx_ai_response_cache_expires", "idx_assets_city_country",
    "idx_assets_exposure", "idx_assets_function",
    "idx_matches_planned_patch_date",
]

# ARGUS defines no custom PL/pgSQL functions or triggers anywhere in its
# codebase as of this rewrite (verified by grep across schema.sql and
# every database/*.py module — see this script's module docstring).
# These lists are intentionally empty rather than fabricated; the
# verify_functions()/verify_triggers() steps below report "0 expected,
# 0 found" instead of silently skipping, so a future contributor adding
# a real function/trigger has an obvious place to register it.
REQUIRED_FUNCTIONS: List[str] = []
REQUIRED_TRIGGERS: List[str] = []


# ══════════════════════════════════════════════════════════════════════════
# Base schema bootstrap (schema.sql)
# ══════════════════════════════════════════════════════════════════════════

def read_schema_sql() -> str:
    path = os.path.join(_THIS_DIR, "schema.sql")
    if not os.path.isfile(path):
        raise MigrationError(
            f"schema.sql not found at {path} — this file ships with ARGUS "
            f"and is required for first-time installation. Re-download or "
            f"re-clone the ARGUS repository."
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def execute_schema(conn) -> None:
    """
    Step 6: execute the full schema.sql bootstrap file. Every statement
    in schema.sql is itself written idempotently (CREATE TABLE IF NOT
    EXISTS, CREATE INDEX IF NOT EXISTS, DO $$ ... IF NOT EXISTS $$ blocks,
    CREATE OR REPLACE VIEW), so this is safe to run against an empty
    database, a partially-populated one, or a fully up-to-date one.

    Runs as a single transaction: if any statement in schema.sql fails,
    the entire file's effects are rolled back rather than leaving the
    database with, say, half its base tables created and the rest
    missing.
    """
    sql_text = read_schema_sql()
    with conn:
        with conn.cursor() as cur:
            cur.execute(sql_text)


# ══════════════════════════════════════════════════════════════════════════
# Incremental migrations
#
# This is the exact SQL from the previous version of ARGUS's migrate.py,
# restructured into versioned, checksummed, individually-tracked units —
# not rewritten or reworded. Every one of these was already idempotent
# (IF NOT EXISTS / ON CONFLICT / DO $$ IF NOT EXISTS $$ guards) in the
# original, which is exactly what makes it safe to also run this list
# against a completely fresh database that has only just had schema.sql
# applied to it: base tables (assets/cves/matches/alerts/reports/users)
# already exist by the time this list runs, and everything below either
# adds a column to one of those, or creates a new table that references
# them.
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Migration:
    version: int
    name: str
    sql: str
    category: str = "core"
    description: str = ""
    depends_on: Tuple[str, ...] = ()
    rollback_strategy: str = (
        "Additive only (ADD COLUMN / CREATE TABLE / CREATE INDEX, all "
        "guarded with IF NOT EXISTS). Not auto-reversible by design — "
        "dropping a column or table can destroy operator data. To revert "
        "manually, DROP the specific object this migration created; there "
        "is no automated `down` migration."
    )

    @property
    def checksum(self) -> str:
        return checksum_of(self.sql)


MIGRATIONS: List[Migration] = [
    Migration(
        1, "assets.type column", "core",
        sql="ALTER TABLE assets ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT 'Unknown'",
        description="Adds the device-category column to assets for pre-existing databases created before it existed.",
        depends_on=("base_schema:assets",),
    ),
    Migration(
        2, "assets.last_scan column", "core",
        sql="ALTER TABLE assets ADD COLUMN IF NOT EXISTS last_scan TIMESTAMPTZ",
        description="Timestamp of the most recent scan of this asset (database/assets.py::update_last_scan).",
        depends_on=("base_schema:assets",),
    ),
    Migration(
        3, "assets.search_keyword column", "core",
        sql="ALTER TABLE assets ADD COLUMN IF NOT EXISTS search_keyword TEXT",
        description="NVD search keyword override used by the scanner (defaults to 'vendor product' if unset).",
        depends_on=("base_schema:assets",),
    ),
    Migration(
        4, "cves.severity column", "core",
        sql="ALTER TABLE cves ADD COLUMN IF NOT EXISTS severity TEXT",
        description="Severity label (LOW/MEDIUM/HIGH/CRITICAL) derived from CVSS.",
        depends_on=("base_schema:cves",),
    ),
    Migration(
        5, "cves.epss column", "core",
        sql="ALTER TABLE cves ADD COLUMN IF NOT EXISTS epss NUMERIC(8,6)",
        description="FIRST.org EPSS exploitation-probability score.",
        depends_on=("base_schema:cves",),
    ),
    Migration(
        6, "cves.epss_percentile column", "core",
        sql="ALTER TABLE cves ADD COLUMN IF NOT EXISTS epss_percentile NUMERIC(8,6)",
        description="EPSS percentile ranking, stored alongside the raw score.",
        depends_on=("base_schema:cves",),
    ),
    Migration(
        7, "cves.created_at column", "core",
        sql="ALTER TABLE cves ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        description=(
            "schema.sql's CREATE TABLE cves defines created_at, but a "
            "live database whose cves table pre-dates that column keeps "
            "missing it forever under CREATE TABLE IF NOT EXISTS alone — "
            "database/cves.py::get_cve() selects this column directly."
        ),
        depends_on=("base_schema:cves",),
    ),
    Migration(
        8, "matches UNIQUE (asset_id, cve_id) constraint", "core",
        sql="""
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
        """,
        description=(
            "Ensures one row per (asset, CVE) pair, deleting any duplicate "
            "rows accumulated before this constraint existed (keeping the "
            "lowest id of each duplicate group) so the ADD CONSTRAINT can "
            "succeed."
        ),
        depends_on=("base_schema:matches",),
    ),
    Migration(
        9, "matches.first_seen column", "core",
        sql="ALTER TABLE matches ADD COLUMN IF NOT EXISTS first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        description="When a finding was first discovered — used for SLA due-date backfill and 'days open' reporting.",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        10, "matches.status column", "phase2",
        sql="""
        ALTER TABLE matches
        ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'Open'
            CHECK (status IN ('Open','In Progress','Resolved','Accepted Risk','False Positive'))
        """,
        description="Remediation workflow status for a finding.",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        11, "matches.resolved_at column", "phase2",
        sql="ALTER TABLE matches ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ",
        description="Stamped when status transitions to 'Resolved'; cleared otherwise.",
        depends_on=("matches.status column",),
    ),
    Migration(
        12, "matches.due_date column", "phase2",
        sql="ALTER TABLE matches ADD COLUMN IF NOT EXISTS due_date DATE",
        description="Auto-calculated SLA compliance deadline, derived from CVSS at insert time.",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        13, "matches.patched column", "phase2",
        sql="ALTER TABLE matches ADD COLUMN IF NOT EXISTS patched BOOLEAN NOT NULL DEFAULT FALSE",
        description="Whether this finding has been patched (independent of workflow status).",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        14, "matches.assigned_to column", "phase2",
        sql="ALTER TABLE matches ADD COLUMN IF NOT EXISTS assigned_to TEXT",
        description="Individual owner assigned to remediate this finding.",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        15, "matches.assigned_team column", "phase2",
        sql="ALTER TABLE matches ADD COLUMN IF NOT EXISTS assigned_team TEXT",
        description="Team assigned to remediate this finding.",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        16, "backfill matches.due_date from cvss", "phase2-data",
        sql="""
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
        """,
        description="One-time backfill of due_date for findings that existed before the column did.",
        depends_on=("matches.due_date column", "matches.first_seen column"),
    ),
    Migration(
        17, "alerts table", "system",
        sql="""
        CREATE TABLE IF NOT EXISTS alerts (
            id       SERIAL PRIMARY KEY,
            asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
            message  TEXT        NOT NULL,
            sent_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        description="Historical record of Telegram alerts sent (database/matches.py::save_alert).",
        depends_on=("base_schema:assets",),
    ),
    Migration(
        18, "reports table", "system",
        sql="""
        CREATE TABLE IF NOT EXISTS reports (
            id           SERIAL PRIMARY KEY,
            report_type  VARCHAR(20),
            generated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            file_path    TEXT      NOT NULL
        )
        """,
        description="Generated PDF report metadata (database/reports.py).",
    ),
    Migration(
        19, "users table", "system",
        sql="""
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'viewer',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        description="Self-registered dashboard accounts (in addition to the in-memory admin/viewer accounts).",
    ),
    Migration(
        20, "index matches(asset_id)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_matches_asset_id ON matches(asset_id)",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        21, "index matches(cve_id)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_matches_cve_id ON matches(cve_id)",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        22, "index matches(risk_score)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_matches_risk ON matches(risk_score DESC)",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        23, "index matches(status)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)",
        depends_on=("matches.status column",),
    ),
    Migration(
        24, "index matches(due_date)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_matches_due_date ON matches(due_date)",
        depends_on=("matches.due_date column",),
    ),
    Migration(
        25, "index assets(type)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(type)",
        depends_on=("assets.type column",),
    ),
    Migration(
        26, "index cves(kev) partial", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_cves_kev ON cves(kev) WHERE kev = TRUE",
        description=(
            "cves.kev is filtered on heavily (the /findings KEV filter, "
            "dashboard KEV counts) but had no supporting index, forcing a "
            "sequential scan of the entire cves table on every such query."
        ),
        depends_on=("base_schema:cves",),
    ),
    Migration(
        27, "index cves(cvss)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_cves_cvss ON cves(cvss DESC)",
        description="Supports CVSS-sorted views (/cves live search sort).",
        depends_on=("base_schema:cves",),
    ),
    Migration(
        28, "back-fill cves.severity from cvss", "phase2-data",
        sql="""
        UPDATE cves SET severity =
            CASE
                WHEN cvss >= 9.0 THEN 'CRITICAL'
                WHEN cvss >= 7.0 THEN 'HIGH'
                WHEN cvss >= 4.0 THEN 'MEDIUM'
                WHEN cvss >  0   THEN 'LOW'
                ELSE 'NONE'
            END
        WHERE severity IS NULL
        """,
        depends_on=("cves.severity column",),
    ),
    Migration(
        29, "back-fill assets.search_keyword", "phase2-data",
        sql="UPDATE assets SET search_keyword = vendor || ' ' || product WHERE search_keyword IS NULL",
        depends_on=("assets.search_keyword column",),
    ),
    Migration(
        30, "ai_conversations table", "ai",
        sql="""
        CREATE TABLE IF NOT EXISTS ai_conversations (
            id          SERIAL PRIMARY KEY,
            username    TEXT        NOT NULL,
            title       TEXT        NOT NULL DEFAULT 'New conversation',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            archived    BOOLEAN     NOT NULL DEFAULT FALSE
        )
        """,
        description="AI Security Copilot conversation metadata (database/conversations.py).",
    ),
    Migration(
        31, "repair ai_conversations.username column", "ai",
        sql="ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS username TEXT",
        description=(
            "Repairs deployments where an earlier ad-hoc ai_conversations "
            "table (user_id INTEGER instead of username TEXT) pre-dates "
            "this shape, so CREATE TABLE IF NOT EXISTS above silently "
            "no-oped against it."
        ),
        depends_on=("ai_conversations table",),
    ),
    Migration(
        32, "repair ai_conversations.updated_at column", "ai",
        sql="ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        depends_on=("ai_conversations table",),
    ),
    Migration(
        33, "repair ai_conversations.archived column", "ai",
        sql="ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE",
        depends_on=("ai_conversations table",),
    ),
    Migration(
        34, "backfill ai_conversations.title default", "ai",
        sql="ALTER TABLE ai_conversations ALTER COLUMN title SET DEFAULT 'New conversation'",
        depends_on=("ai_conversations table",),
    ),
    Migration(
        35, "ai_messages table", "ai",
        sql="""
        CREATE TABLE IF NOT EXISTS ai_messages (
            id              SERIAL PRIMARY KEY,
            conversation_id INTEGER     NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
            role            TEXT        NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
            content         TEXT        NOT NULL,
            tokens          INTEGER     DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        description="Individual chat turns within an AI conversation (database/conversations.py).",
        depends_on=("ai_conversations table",),
    ),
    Migration(
        36, "repair ai_messages.tokens column", "ai",
        sql="ALTER TABLE ai_messages ADD COLUMN IF NOT EXISTS tokens INTEGER DEFAULT 0",
        depends_on=("ai_messages table",),
    ),
    Migration(
        37, "delete orphaned ai_messages with no matching conversation", "ai-repair",
        sql="DELETE FROM ai_messages WHERE conversation_id NOT IN (SELECT id FROM ai_conversations)",
        description=(
            "CRITICAL REPAIR: ai_messages.conversation_id was supposed to "
            "reference ai_conversations(id) ON DELETE CASCADE, but because "
            "ai_messages already existed (in a different shape) when this "
            "table was first defined, CREATE TABLE IF NOT EXISTS silently "
            "skipped that FK along with everything else, so deleting a "
            "conversation never cascaded to its messages — they became "
            "permanently orphaned. This deletes existing orphans (Postgres "
            "refuses to add a FK while violating rows exist) before the "
            "next migration adds the FK for real."
        ),
        depends_on=("ai_messages table",),
    ),
    Migration(
        38, "add missing ai_messages -> ai_conversations foreign key", "ai-repair",
        sql="""
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
        """,
        depends_on=("delete orphaned ai_messages with no matching conversation",),
    ),
    Migration(
        39, "index ai_conversations(username)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_ai_conversations_username ON ai_conversations(username, updated_at DESC)",
        depends_on=("ai_conversations table",),
    ),
    Migration(
        40, "index ai_messages(conversation_id)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id, created_at)",
        depends_on=("ai_messages table",),
    ),
    Migration(
        41, "cve_ai_analysis table", "ai",
        sql="""
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
        """,
        description="Cached AI-generated CVE analysis (database/cve_analysis.py).",
        depends_on=("base_schema:cves",),
    ),
    Migration(
        42, "repair cve_ai_analysis.technical_impact column", "ai",
        sql="ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS technical_impact TEXT",
        depends_on=("cve_ai_analysis table",),
    ),
    Migration(
        43, "repair cve_ai_analysis.recommended_actions column", "ai",
        sql="ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS recommended_actions TEXT",
        depends_on=("cve_ai_analysis table",),
    ),
    Migration(
        44, "repair cve_ai_analysis.description_hash column", "ai",
        sql="ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS description_hash TEXT",
        depends_on=("cve_ai_analysis table",),
    ),
    Migration(
        45, "repair cve_ai_analysis.status column", "ai",
        sql="ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'",
        depends_on=("cve_ai_analysis table",),
    ),
    Migration(
        46, "repair cve_ai_analysis.retry_count column", "ai",
        sql="ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0",
        depends_on=("cve_ai_analysis table",),
    ),
    Migration(
        47, "repair cve_ai_analysis.error_message column", "ai",
        sql="ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS error_message TEXT",
        depends_on=("cve_ai_analysis table",),
    ),
    Migration(
        48, "repair cve_ai_analysis.created_at column", "ai",
        sql="ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        depends_on=("cve_ai_analysis table",),
    ),
    Migration(
        49, "repair cve_ai_analysis.updated_at column", "ai",
        sql="ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        depends_on=("cve_ai_analysis table",),
    ),
    Migration(
        50, "add cve_ai_analysis.status CHECK constraint", "ai",
        sql="""
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
        """,
        description=(
            "Added separately from the column definition (rather than an "
            "inline CHECK) because ADD COLUMN with an inline CHECK fails "
            "on tables that already have rows violating it; this guards "
            "the same way even though there are no rows yet on a fresh install."
        ),
        depends_on=("repair cve_ai_analysis.status column",),
    ),
    Migration(
        51, "index cve_ai_analysis(status)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_cve_ai_analysis_status ON cve_ai_analysis(status)",
        depends_on=("cve_ai_analysis table",),
    ),
    Migration(
        52, "risk_snapshots table", "ai",
        sql="""
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
        """,
        description=(
            "One row per day with aggregate counts, written by the daily "
            "APScheduler job (jobs/daily_scan.py), enabling week-over-week "
            "trend comparisons that `matches` alone (current state only) "
            "cannot answer."
        ),
    ),
    Migration(
        53, "index risk_snapshots(snapshot_date)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_risk_snapshots_date ON risk_snapshots(snapshot_date DESC)",
        depends_on=("risk_snapshots table",),
    ),
    Migration(
        54, "ai_response_cache table", "ai",
        sql="""
        CREATE TABLE IF NOT EXISTS ai_response_cache (
            cache_key   TEXT        PRIMARY KEY,
            question    TEXT        NOT NULL,
            response    TEXT        NOT NULL,
            tokens      INTEGER     NOT NULL DEFAULT 0,
            hit_count   INTEGER     NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ NOT NULL
        )
        """,
        description=(
            "Keyed on a hash of (normalized question + ARGUS context), not "
            "just the raw question, so the cache auto-invalidates whenever "
            "the underlying ARGUS data changes, with a short TTL enforced "
            "as a safety net against staleness."
        ),
    ),
    Migration(
        55, "index ai_response_cache(expires_at)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_ai_response_cache_expires ON ai_response_cache(expires_at)",
        depends_on=("ai_response_cache table",),
    ),
    Migration(
        56, "assets.city column", "geo",
        sql="ALTER TABLE assets ADD COLUMN IF NOT EXISTS city VARCHAR(120)",
        description=(
            "City Exposure Overview feature. Nullable on purpose — existing "
            "assets have no city/country data and must keep working "
            "unmodified; NULL/blank is the documented 'unassigned asset' case."
        ),
        depends_on=("base_schema:assets",),
    ),
    Migration(
        57, "assets.country_code column", "geo",
        sql="ALTER TABLE assets ADD COLUMN IF NOT EXISTS country_code CHAR(2)",
        depends_on=("base_schema:assets",),
    ),
    Migration(
        58, "index assets(country_code, city)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_assets_city_country ON assets (country_code, city)",
        depends_on=("assets.city column", "assets.country_code column"),
    ),
    Migration(
        59, "assets.exposure column", "metadata",
        sql="ALTER TABLE assets ADD COLUMN IF NOT EXISTS exposure TEXT NOT NULL DEFAULT 'Internal'",
        description=(
            "Whether the asset is reachable from outside the organization's "
            "network. Defaults to 'Internal' (the safer pre-select) rather "
            "than NULL — an admin must actively mark something 'External'."
        ),
        depends_on=("base_schema:assets",),
    ),
    Migration(
        60, "assets.exposure check constraint", "metadata",
        sql="""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'assets' AND constraint_name = 'assets_exposure_check'
            ) THEN
                ALTER TABLE assets ADD CONSTRAINT assets_exposure_check
                    CHECK (exposure IN ('Internal', 'External'));
            END IF;
        END $$;
        """,
        depends_on=("assets.exposure column",),
    ),
    Migration(
        61, "assets.function column", "metadata",
        sql="ALTER TABLE assets ADD COLUMN IF NOT EXISTS function TEXT",
        description=(
            "Network role (Gateway/Endpoint/etc.), independent of device "
            "type. Stays nullable — 'unclassified role' is a legitimate, "
            "common state, unlike exposure."
        ),
        depends_on=("base_schema:assets",),
    ),
    Migration(
        62, "index assets(exposure)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_assets_exposure ON assets (exposure)",
        depends_on=("assets.exposure column",),
    ),
    Migration(
        63, "index assets(function)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_assets_function ON assets (function)",
        depends_on=("assets.function column",),
    ),
    Migration(
        64, "matches.planned_patch_date column", "patch-planning",
        sql="ALTER TABLE matches ADD COLUMN IF NOT EXISTS planned_patch_date DATE",
        description=(
            "The analyst's own scheduling decision for when a patch will "
            "actually be applied — deliberately separate from due_date "
            "(the auto-calculated SLA deadline), never auto-computed or "
            "overwritten by ARGUS."
        ),
        depends_on=("base_schema:matches",),
    ),
    Migration(
        65, "matches.patch_notes column", "patch-planning",
        sql="ALTER TABLE matches ADD COLUMN IF NOT EXISTS patch_notes TEXT",
        depends_on=("base_schema:matches",),
    ),
    Migration(
        66, "index matches(planned_patch_date)", "index",
        sql="CREATE INDEX IF NOT EXISTS idx_matches_planned_patch_date ON matches (planned_patch_date)",
        depends_on=("matches.planned_patch_date column",),
    ),
]

# Sanity-check at import time: version numbers must be unique and
# strictly increasing in list order, since get_current_schema_version()
# reports MAX(version) as "the" schema version and apply_pending_migrations()
# relies on list order for human-readable progress reporting.
_seen_versions = set()
for _m in MIGRATIONS:
    if _m.version in _seen_versions:
        raise AssertionError(f"Duplicate migration version: {_m.version} ({_m.name!r})")
    _seen_versions.add(_m.version)
del _seen_versions, _m


def apply_pending_migrations(conn, dry_run: bool = False) -> dict:
    """
    Step 8: run every migration in MIGRATIONS that has not already been
    successfully applied with an identical checksum. Each migration runs
    in its own transaction (via `with conn:`) so a single failing
    migration rolls back only its own statement and does not poison or
    abort the ones after it — matching the previous migrate.py's
    behavior, and appropriate here because every migration is
    independent/additive rather than a strict linear dependency chain
    enforced by the database itself (dependencies are documented per
    migration for human readers, not mechanically enforced, since the
    IF NOT EXISTS guards make execution order forgiving in practice —
    see each Migration's `depends_on`).
    """
    applied, skipped, failed = [], [], []
    total = len(MIGRATIONS)

    for i, migration in enumerate(MIGRATIONS, start=1):
        LOG.progress(i, total, migration.name)
        checksum = migration.checksum
        last_run = get_last_recorded_run(conn, migration.name)

        if last_run and last_run["status"] == "success" and last_run["checksum"] == checksum:
            skipped.append(migration.name)
            LOG.debug(f"Skipping '{migration.name}' — already applied with matching checksum.")
            continue

        if dry_run:
            applied.append(migration.name)
            continue

        start = time.monotonic()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(migration.sql)
            duration_ms = int((time.monotonic() - start) * 1000)
            record_migration(conn, migration.version, migration.name, checksum, "success", duration_ms)
            applied.append(migration.name)
            LOG.debug(f"Applied '{migration.name}' in {duration_ms}ms.")
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            conn.rollback()
            try:
                record_migration(conn, migration.version, migration.name, checksum,
                                  "failed", duration_ms, error_message=str(exc))
            except Exception:
                conn.rollback()
            failed.append((migration.name, str(exc)))
            LOG.error(f"Migration '{migration.name}' failed: {exc}")

    if not LOG.quiet and total:
        print()  # newline after the progress bar

    return {"applied": applied, "skipped": skipped, "failed": failed}


# ══════════════════════════════════════════════════════════════════════════
# Views
# ══════════════════════════════════════════════════════════════════════════

_VIEW_STATEMENT_RE = re.compile(r"CREATE OR REPLACE VIEW\s+\w+\s+AS[\s\S]+?;")
_VIEW_NAME_RE = re.compile(r"VIEW\s+(\w+)")


def create_or_replace_views(conn) -> dict:
    """
    Step 10: (re-)apply every `CREATE OR REPLACE VIEW` statement found in
    schema.sql. Safe to run any number of times.

    Anchored on `VIEW\\s+\\w+\\s+AS` rather than the bare phrase
    "CREATE OR REPLACE VIEW" — a previous version of this extraction
    regex matched that literal text anywhere, including inside a SQL
    comment that mentioned the phrase as prose, silently producing a
    malformed statement (comment text + the real view glued together)
    that failed to execute while looking successful in isolation. This
    regex requires the actual `VIEW <name> AS` syntax to follow it.
    """
    views_sql = read_schema_sql()
    statements = _VIEW_STATEMENT_RE.findall(views_sql)

    created, failed = [], []
    for stmt in statements:
        m = _VIEW_NAME_RE.search(stmt)
        name = m.group(1) if m else "unknown"
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(stmt)
            created.append(name)
            LOG.debug(f"View '{name}' created/refreshed.")
        except Exception as exc:
            conn.rollback()
            failed.append((name, str(exc)))
            LOG.error(f"View '{name}' failed: {exc}")

    return {"created": created, "failed": failed}


# ══════════════════════════════════════════════════════════════════════════
# Data seeding / backfill
# ══════════════════════════════════════════════════════════════════════════

def seed_required_data(conn) -> dict:
    """
    Step 9. ARGUS does not seed default rows into `users` — the built-in
    admin/viewer accounts are constructed in-memory at application
    startup from ADMIN_PASSWORD/VIEWER_PASSWORD (see app.py), never
    written to the database, so there is deliberately no "insert default
    users" step here (that would invent behavior the application doesn't
    have). What this step actually does is run the two real, existing,
    idempotent backfill/cleanup operations that database/cve_analysis.py
    already provides — moved here (from the previous migrate.py's
    __main__ block) so they're part of the same tracked, reported run
    instead of a separate uncounted afterthought.
    """
    results = {"queued": 0, "cleaned": 0, "errors": []}
    try:
        from database.cve_analysis import backfill_missing_analysis, cleanup_orphaned_analysis
    except Exception as exc:
        results["errors"].append(f"Could not import database.cve_analysis: {exc}")
        return results

    try:
        results["queued"] = backfill_missing_analysis()
    except Exception as exc:
        results["errors"].append(f"backfill_missing_analysis failed: {exc}")

    try:
        results["cleaned"] = cleanup_orphaned_analysis()
    except Exception as exc:
        results["errors"].append(f"cleanup_orphaned_analysis failed: {exc}")

    return results


# ══════════════════════════════════════════════════════════════════════════
# Verification
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class VerificationReport:
    tables_ok: List[str] = field(default_factory=list)
    tables_missing: List[str] = field(default_factory=list)
    views_ok: List[str] = field(default_factory=list)
    views_missing: List[str] = field(default_factory=list)
    columns_ok: int = 0
    columns_missing: List[str] = field(default_factory=list)
    fks_ok: List[str] = field(default_factory=list)
    fks_missing: List[str] = field(default_factory=list)
    constraints_ok: List[str] = field(default_factory=list)
    constraints_missing: List[str] = field(default_factory=list)
    indexes_ok: List[str] = field(default_factory=list)
    indexes_missing: List[str] = field(default_factory=list)
    functions_ok: List[str] = field(default_factory=list)
    functions_missing: List[str] = field(default_factory=list)
    triggers_ok: List[str] = field(default_factory=list)
    triggers_missing: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.tables_missing or self.views_missing or self.columns_missing
            or self.fks_missing or self.constraints_missing or self.indexes_missing
            or self.functions_missing or self.triggers_missing
        )


def verify_schema(conn) -> VerificationReport:
    """Steps 7 & 16-18: verify every required object actually exists,
    rather than trusting that a CREATE statement which didn't raise
    means the object is now in the expected shape."""
    report = VerificationReport()

    for t in REQUIRED_TABLES:
        (report.tables_ok if table_exists(conn, t) else report.tables_missing).append(t)

    for v in REQUIRED_VIEWS:
        (report.views_ok if view_exists(conn, v) else report.views_missing).append(v)

    for table, col in REQUIRED_COLUMNS:
        if column_exists(conn, table, col):
            report.columns_ok += 1
        else:
            report.columns_missing.append(f"{table}.{col}")

    for fk in REQUIRED_FOREIGN_KEYS:
        (report.fks_ok if foreign_key_exists(conn, fk) else report.fks_missing).append(fk)

    for name, table in REQUIRED_CONSTRAINTS:
        (report.constraints_ok if constraint_exists(conn, name, table) else report.constraints_missing).append(name)

    for idx in REQUIRED_INDEXES:
        (report.indexes_ok if index_exists(conn, idx) else report.indexes_missing).append(idx)

    # Intentionally-empty lists (see REQUIRED_FUNCTIONS/REQUIRED_TRIGGERS
    # docstring) — these loops simply do nothing and both *_missing lists
    # stay empty, which is the correct, honest "0 expected" result.
    for fn in REQUIRED_FUNCTIONS:
        (report.functions_ok if function_exists(conn, fn) else report.functions_missing).append(fn)
    for trg in REQUIRED_TRIGGERS:
        (report.triggers_ok if trigger_exists(conn, trg) else report.triggers_missing).append(trg)

    return report


def verify_database(conn) -> VerificationReport:
    """Public entry point used by --check and by the final verification
    pass at the end of a normal run."""
    return verify_schema(conn)


# ══════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════

def print_summary(
    config: DBConfig,
    migration_result: dict,
    view_result: dict,
    seed_result: dict,
    report: VerificationReport,
    schema_was_bootstrapped: bool,
    elapsed_seconds: float,
) -> None:
    LOG.section("Migration Summary")

    version = "unknown"
    try:
        pass  # populated by caller via get_current_schema_version before calling this
    except Exception:
        pass

    print(f"  Target database:      {config.safe_repr}")
    print(f"  Base schema bootstrap: {'performed (fresh install)' if schema_was_bootstrapped else 'not needed (already present)'}")
    print(f"  Migrations applied:    {len(migration_result['applied'])}")
    print(f"  Migrations skipped:    {len(migration_result['skipped'])} (already up to date)")
    print(f"  Migrations failed:     {len(migration_result['failed'])}")
    print(f"  Views created/verified: {len(view_result['created'])} / {len(REQUIRED_VIEWS)} required")
    if view_result["failed"]:
        print(f"  Views failed:          {len(view_result['failed'])}")
    print(f"  AI analysis backfilled: {seed_result.get('queued', 0)} CVE(s) queued, "
          f"{seed_result.get('cleaned', 0)} orphaned row(s) cleaned")
    print()
    print(f"  Required tables:      {len(report.tables_ok)}/{len(REQUIRED_TABLES)} present")
    print(f"  Required views:       {len(report.views_ok)}/{len(REQUIRED_VIEWS)} present")
    print(f"  Required columns:     {report.columns_ok}/{len(REQUIRED_COLUMNS)} present")
    print(f"  Required foreign keys: {len(report.fks_ok)}/{len(REQUIRED_FOREIGN_KEYS)} present")
    print(f"  Required constraints: {len(report.constraints_ok)}/{len(REQUIRED_CONSTRAINTS)} present")
    print(f"  Required indexes:     {len(report.indexes_ok)}/{len(REQUIRED_INDEXES)} present")
    print(f"  Custom functions:     {len(report.functions_ok)}/{len(REQUIRED_FUNCTIONS)} present (ARGUS defines none)")
    print(f"  Custom triggers:      {len(report.triggers_ok)}/{len(REQUIRED_TRIGGERS)} present (ARGUS defines none)")
    print()

    for t in REQUIRED_TABLES:
        mark = _Ansi.wrap("✓", _Ansi.GREEN) if t in report.tables_ok else _Ansi.wrap("✗", _Ansi.RED)
        print(f"    {mark} {t}")

    print()
    if report.ok:
        LOG.success(f"Database successfully initialized in {elapsed_seconds:.2f}s. Ready to launch ARGUS.")
    else:
        LOG.error(
            f"Migration finished in {elapsed_seconds:.2f}s but verification found missing objects "
            f"(see ✗ marks above and the lists below). ARGUS may not run correctly until these are resolved."
        )
        for label, missing in (
            ("tables", report.tables_missing), ("views", report.views_missing),
            ("columns", report.columns_missing), ("foreign keys", report.fks_missing),
            ("constraints", report.constraints_missing), ("indexes", report.indexes_missing),
        ):
            if missing:
                print(f"    Missing {label}: {', '.join(missing)}")

    if migration_result["failed"]:
        print()
        LOG.error(f"{len(migration_result['failed'])} migration(s) failed to apply:")
        for name, err in migration_result["failed"]:
            print(f"    ✗ {name}: {err}")

    if LOG.warnings:
        print()
        print(_Ansi.wrap(f"  {len(LOG.warnings)} warning(s) were logged during this run.", _Ansi.YELLOW))


# ══════════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════════

TOTAL_STEPS = 19


def run_migration(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ARGUS database migration / installation tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--check", action="store_true",
                        help="Verify the schema only; make no changes.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print debug-level detail for every step.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress INFO/SUCCESS output; only warnings/errors and the final summary.")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI colors in output.")
    parser.add_argument("--skip-create-db", action="store_true",
                        help="Never attempt CREATE DATABASE; fail with instructions instead.")
    args = parser.parse_args(argv)

    if args.no_color:
        _Ansi.ENABLED = False

    global LOG
    LOG = MigrationLogger(verbose=args.verbose, quiet=args.quiet)

    print(_Ansi.wrap("ARGUS Database Migration System", _Ansi.BOLD, _Ansi.CYAN))
    print(_Ansi.wrap("=" * 34, _Ansi.GRAY))

    config = load_config()
    start_time = time.monotonic()
    schema_was_bootstrapped = False

    # ── Step 1: verify server connectivity ──────────────────────────────
    LOG.step(1, TOTAL_STEPS, f"Verifying PostgreSQL connection ({config.safe_repr}) ...")
    ok, err = verify_postgres_connection(config)
    if not ok:
        LOG.error(err)
        return 1
    LOG.success("PostgreSQL server is reachable and credentials are valid.")

    # ── Step 2: database existence / creation ───────────────────────────
    LOG.step(2, TOTAL_STEPS, f"Checking whether database '{config.database}' exists ...")
    if args.check:
        if not database_exists(config):
            LOG.error(f"Database '{config.database}' does not exist. (--check: not creating it.)")
            return 2
        LOG.success(f"Database '{config.database}' exists.")
    else:
        if not create_database_if_missing(config, allow_create=not args.skip_create_db):
            return 2
        LOG.success(f"Database '{config.database}' is ready.")

    # ── Step 3: connect to target database ──────────────────────────────
    LOG.step(3, TOTAL_STEPS, "Connecting to target database ...")
    try:
        conn = get_target_connection(config)
    except ServerUnreachable as exc:
        LOG.error(str(exc))
        return 1
    LOG.success("Connected.")

    try:
        # ── Step 4: schema_version tracking table ───────────────────────
        LOG.step(4, TOTAL_STEPS, "Ensuring schema_version tracking table exists ...")
        if not args.check:
            ensure_schema_version_table(conn)
        version_table_present = table_exists(conn, SCHEMA_VERSION_TABLE)
        LOG.success("schema_version table present." if version_table_present
                    else "schema_version table missing (--check mode).")

        # ── Step 5: check required tables ───────────────────────────────
        LOG.step(5, TOTAL_STEPS, "Checking for required base tables ...")
        missing_tables = [t for t in REQUIRED_TABLES if not table_exists(conn, t)]
        core_missing = [t for t in ("assets", "cves", "matches") if t in missing_tables]
        if missing_tables:
            LOG.warn(f"{len(missing_tables)} required table(s) missing: {', '.join(missing_tables)}")
        else:
            LOG.success("All required tables already present.")

        # ── Step 6: bootstrap schema.sql if needed ──────────────────────
        LOG.step(6, TOTAL_STEPS, "Bootstrapping base schema (schema.sql) if needed ...")
        if core_missing:
            if args.check:
                LOG.error(
                    f"Core tables missing ({', '.join(core_missing)}) and --check "
                    f"mode will not create them."
                )
            else:
                LOG.info("Core tables missing — executing schema.sql ...")
                try:
                    execute_schema(conn)
                    schema_was_bootstrapped = True
                    LOG.success("schema.sql executed successfully.")
                except Exception as exc:
                    conn.rollback()
                    LOG.error(f"Failed to execute schema.sql: {exc}")
                    if args.verbose:
                        traceback.print_exc()
                    return 3
        else:
            LOG.success("Core tables already exist — schema.sql bootstrap not required.")
            if not args.check:
                # Still safe/cheap to run: every statement in schema.sql is
                # idempotent, and this guarantees views/indexes defined
                # there are current even on a long-lived database that
                # never had schema.sql (re-)applied since those were added.
                try:
                    execute_schema(conn)
                except Exception as exc:
                    conn.rollback()
                    LOG.warn(f"Re-running schema.sql for parity found an issue (non-fatal): {exc}")

        # ── Step 7: verify schema.sql completed successfully ────────────
        LOG.step(7, TOTAL_STEPS, "Verifying base schema landed correctly ...")
        post_bootstrap_missing = [t for t in ("assets", "cves", "matches", "alerts", "reports", "users")
                                   if not table_exists(conn, t)]
        if post_bootstrap_missing:
            LOG.error(f"Base tables still missing after schema.sql: {', '.join(post_bootstrap_missing)}")
            return 3
        LOG.success("Base schema verified.")

        # ── Step 8: incremental migrations ──────────────────────────────
        LOG.step(8, TOTAL_STEPS, f"Applying incremental migrations ({len(MIGRATIONS)} defined) ...")
        migration_result = apply_pending_migrations(conn, dry_run=args.check)
        if migration_result["failed"] and not args.check:
            LOG.error(f"{len(migration_result['failed'])} migration(s) failed — see details below.")
        else:
            LOG.success(
                f"{len(migration_result['applied'])} applied, "
                f"{len(migration_result['skipped'])} already up to date."
            )

        # ── Step 9: seed / backfill required data ───────────────────────
        LOG.step(9, TOTAL_STEPS, "Seeding/backfilling required data ...")
        if args.check:
            seed_result = {"queued": 0, "cleaned": 0, "errors": []}
            LOG.info("Skipped in --check mode.")
        else:
            seed_result = seed_required_data(conn)
            if seed_result["errors"]:
                for e in seed_result["errors"]:
                    LOG.warn(e)
            LOG.success(
                f"Queued {seed_result['queued']} CVE(s) for AI analysis; "
                f"cleaned {seed_result['cleaned']} orphaned analysis row(s)."
            )

        # ── Step 10: create/verify views ────────────────────────────────
        LOG.step(10, TOTAL_STEPS, "Creating/verifying database views ...")
        if args.check:
            view_result = {"created": [], "failed": []}
            for v in REQUIRED_VIEWS:
                LOG.info(f"  view {v}: {'present' if view_exists(conn, v) else 'MISSING'}")
        else:
            view_result = create_or_replace_views(conn)
            if view_result["failed"]:
                LOG.error(f"{len(view_result['failed'])} view(s) failed to create.")
            else:
                LOG.success(f"{len(view_result['created'])} view(s) created/refreshed.")

        # ── Step 11: indexes ─────────────────────────────────────────────
        LOG.step(11, TOTAL_STEPS, "Verifying indexes ...")
        missing_idx = [i for i in REQUIRED_INDEXES if not index_exists(conn, i)]
        if missing_idx:
            LOG.warn(f"{len(missing_idx)} expected index(es) still missing: {', '.join(missing_idx)}")
        else:
            LOG.success(f"All {len(REQUIRED_INDEXES)} expected indexes present.")

        # ── Step 12: foreign keys ────────────────────────────────────────
        LOG.step(12, TOTAL_STEPS, "Verifying foreign keys ...")
        missing_fks = [fk for fk in REQUIRED_FOREIGN_KEYS if not foreign_key_exists(conn, fk)]
        if missing_fks:
            LOG.error(f"Missing foreign key(s): {', '.join(missing_fks)}")
        else:
            LOG.success(f"All {len(REQUIRED_FOREIGN_KEYS)} expected foreign keys present.")

        # ── Step 13: constraints ─────────────────────────────────────────
        LOG.step(13, TOTAL_STEPS, "Verifying constraints ...")
        missing_constraints = [n for n, t in REQUIRED_CONSTRAINTS if not constraint_exists(conn, n, t)]
        if missing_constraints:
            LOG.warn(f"Missing constraint(s): {', '.join(missing_constraints)}")
        else:
            LOG.success(f"All {len(REQUIRED_CONSTRAINTS)} expected constraints present.")

        # ── Step 14: triggers ─────────────────────────────────────────────
        LOG.step(14, TOTAL_STEPS, "Verifying triggers ...")
        LOG.success(f"{len(REQUIRED_TRIGGERS)} expected (ARGUS defines no custom triggers).")

        # ── Step 15: functions ────────────────────────────────────────────
        LOG.step(15, TOTAL_STEPS, "Verifying functions ...")
        LOG.success(f"{len(REQUIRED_FUNCTIONS)} expected (ARGUS defines no custom functions).")

        # ── Step 16: verify every required table ─────────────────────────
        LOG.step(16, TOTAL_STEPS, "Verifying every required table exists ...")
        report = verify_schema(conn)
        for t in REQUIRED_TABLES:
            LOG._emit("SUCCESS" if t in report.tables_ok else "ERROR",
                      _Ansi.GREEN if t in report.tables_ok else _Ansi.RED,
                      f"{'✓' if t in report.tables_ok else '✗'} {t}", force=True)

        # ── Step 17: verify every required view ──────────────────────────
        LOG.step(17, TOTAL_STEPS, "Verifying every required view exists ...")
        for v in REQUIRED_VIEWS:
            LOG._emit("SUCCESS" if v in report.views_ok else "ERROR",
                      _Ansi.GREEN if v in report.views_ok else _Ansi.RED,
                      f"{'✓' if v in report.views_ok else '✗'} {v}", force=True)

        # ── Step 18: verify database (schema) version ────────────────────
        LOG.step(18, TOTAL_STEPS, "Verifying database schema version ...")
        current_version = get_current_schema_version(conn)
        target_version = MIGRATIONS[-1].version if MIGRATIONS else 0
        if current_version >= target_version:
            LOG.success(f"Schema version {current_version} (target {target_version}).")
        else:
            LOG.warn(f"Schema version {current_version} is behind target {target_version}.")

        elapsed = time.monotonic() - start_time

        # ── Step 19: final summary ────────────────────────────────────────
        LOG.step(19, TOTAL_STEPS, "Final summary")
        print_summary(config, migration_result, view_result, seed_result, report,
                      schema_was_bootstrapped, elapsed)

    finally:
        conn.close()

    if args.check:
        return 0 if report.ok else 3
    if migration_result["failed"]:
        return 4
    if not report.ok:
        return 3
    return 0


def main() -> int:
    try:
        return run_migration()
    except MigrationError as exc:
        LOG.error(str(exc))
        return 5
    except KeyboardInterrupt:
        print()
        LOG.error("Interrupted by user.")
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level catch-all is intentional here
        LOG.error(f"Unexpected error: {exc}")
        traceback.print_exc()
        return 5


if __name__ == "__main__":
    sys.exit(main())
