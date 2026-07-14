"""
db.py — PostgreSQL connection factory for ARGUS, backed by a connection pool.

Configuration via environment variables (set in .env):
    DB_HOST          default: localhost
    DB_NAME          default: argus_db
    DB_USER          default: postgres
    DB_PASSWORD      required — no hardcoded default
    DB_PORT          default: 5432
    DB_POOL_MIN_CONN default: 2
    DB_POOL_MAX_CONN default: 20

Why a pool: every database/*.py function in this codebase opens a fresh
connection with get_connection() and closes it when done (one open/close
per query, in some routes a dozen+ times per request). Without pooling,
each of those is a brand-new TCP handshake + PostgreSQL authentication
round-trip — the single largest avoidable latency cost in this database
layer, and a hard ceiling on how many concurrent requests the database
itself can sustain (PostgreSQL's own default max_connections is 100;
unpooled, that caps ARGUS at roughly 100 concurrent in-flight queries
total, platform-wide, regardless of how many app workers are running).

get_connection() / conn.close() keep their exact existing call signature
so every existing caller across the database/ package (assets.py,
matches.py, cves.py, conversations.py, chat_cache.py, cve_analysis.py,
risk_snapshots.py, reports.py, migrate.py — all of which do
`conn = get_connection() ... conn.close()` in a try/finally) keeps
working completely unchanged. The "close" is intercepted and returns the
underlying connection to the pool instead of tearing down the socket.
"""

import os
import logging

import psycopg2
import psycopg2.pool
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_password = os.getenv("DB_PASSWORD")
if not _password:
    logger.warning(
        "DB_PASSWORD environment variable is not set. "
        "Set it in .env or the environment before starting ARGUS."
    )

DB_CONFIG = {
    "host":            os.getenv("DB_HOST", "localhost"),
    "database":        os.getenv("DB_NAME", "argus_db"),
    "user":            os.getenv("DB_USER", "postgres"),
    "password":        _password or "",
    "port":            int(os.getenv("DB_PORT", 5432)),
    "connect_timeout": 5,
}

_POOL_MIN_CONN = int(os.getenv("DB_POOL_MIN_CONN", 2))
_POOL_MAX_CONN = int(os.getenv("DB_POOL_MAX_CONN", 20))

_pool: "psycopg2.pool.ThreadedConnectionPool | None" = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Lazily create the process-wide connection pool on first use."""
    global _pool
    if _pool is None:
        try:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                _POOL_MIN_CONN, _POOL_MAX_CONN, **DB_CONFIG
            )
            logger.info(
                "Initialized PostgreSQL connection pool (min=%d, max=%d).",
                _POOL_MIN_CONN, _POOL_MAX_CONN,
            )
        except psycopg2.Error as exc:
            logger.error("Failed to initialize database connection pool: %s", exc)
            raise
    return _pool


class _PooledConnection:
    """
    Thin wrapper around a pooled psycopg2 connection.

    Proxies every attribute access (cursor(), commit(), rollback(),
    context-manager protocol, etc.) straight through to the real
    connection, so it is a drop-in replacement for what
    psycopg2.connect() used to return. The one behavior that's
    overridden is close(): instead of tearing down the socket, it
    returns the connection to the pool for reuse by the next caller.

    A connection that errored out (psycopg2 sets conn.closed != 0, or a
    transaction was left in a bad state) is discarded from the pool
    rather than returned to it, so one bad connection can never poison
    the pool for subsequent callers.
    """

    __slots__ = ("_conn", "_pool", "_returned")

    def __init__(self, conn, pool):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_returned", False)

    def close(self):
        if self._returned:
            return
        object.__setattr__(self, "_returned", True)
        try:
            if self._conn.closed:
                self._pool.putconn(self._conn, close=True)
            else:
                if self._conn.status != psycopg2.extensions.STATUS_READY:
                    try:
                        self._conn.rollback()
                    except psycopg2.Error:
                        self._pool.putconn(self._conn, close=True)
                        return
                self._pool.putconn(self._conn)
        except Exception as exc:
            logger.warning("Failed to return connection to pool cleanly: %s", exc)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


def get_connection() -> "_PooledConnection":
    """
    Borrow a connection from the pool.

    Returns a _PooledConnection that behaves like a normal psycopg2
    connection in every respect (cursor(), with-statement transaction
    handling, commit/rollback), except that calling .close() on it
    returns the connection to the pool instead of closing the socket.

    Raises psycopg2.OperationalError on failure — callers must handle it
    or let it propagate to Flask / the Telegram error handler, same as
    before.
    """
    pool = _get_pool()
    try:
        raw_conn = pool.getconn()
        return _PooledConnection(raw_conn, pool)
    except psycopg2.Error as exc:
        logger.error("Database connection failed: %s", exc)
        raise


def closeall() -> None:
    """
    Close every connection in the pool. Intended for graceful shutdown
    (e.g. an atexit hook or test teardown) — not required for normal
    request handling.
    """
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("Closed all connections in the database pool.")