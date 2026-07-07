#!/usr/bin/env python3
"""
Argus database migration script.
Run once: cd argus/bot && python database/migrate.py
Fully idempotent — safe to run multiple times.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import get_connection

# ── Core schema migrations (original) ────────────────────────────────────────
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
    # schema.sql's CREATE TABLE cves defines created_at, but the live
    # database's cves table pre-dates that column (CREATE TABLE IF NOT
    # EXISTS is a no-op against the already-existing table, so it never got
    # added). Without this repair, any query selecting created_at from
    # cves -- e.g. database/cves.py's get_cve() -- fails with
    # 'column "created_at" does not exist', which is exactly what caused
    # the scheduler/analyzer errors ("Failed to fetch CVE", "Unexpected
    # error analyzing CVE-...").
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

    # ── Phase 2: Remediation tracking ────────────────────────────────────────
    ("matches.status column",
     """
     ALTER TABLE matches
     ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'Open'
         CHECK (status IN ('Open','In Progress','Resolved','Accepted Risk','False Positive'))
     """),
    ("matches.resolved_at column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ"),
    ("matches.due_date column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS due_date DATE"),
    ("matches.patched column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS patched BOOLEAN NOT NULL DEFAULT FALSE"),

    # ── Phase 2: Ownership & Assignment ──────────────────────────────────────
    ("matches.assigned_to column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS assigned_to TEXT"),
    ("matches.assigned_team column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS assigned_team TEXT"),

    # ── Backfill SLA due_date for existing Open findings ─────────────────────
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

    # ── System tables ─────────────────────────────────────────────────────────
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

    # ── Indexes ───────────────────────────────────────────────────────────────
    ("index matches(asset_id)",   "CREATE INDEX IF NOT EXISTS idx_matches_asset_id ON matches(asset_id)"),
    ("index matches(cve_id)",     "CREATE INDEX IF NOT EXISTS idx_matches_cve_id ON matches(cve_id)"),
    ("index matches(risk_score)", "CREATE INDEX IF NOT EXISTS idx_matches_risk ON matches(risk_score DESC)"),
    ("index matches(status)",     "CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)"),
    ("index matches(due_date)",   "CREATE INDEX IF NOT EXISTS idx_matches_due_date ON matches(due_date)"),
    ("index assets(type)",        "CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(type)"),
    # cves.kev and cves.cvss are filtered/sorted on heavily by app.py
    # (/findings KEV filter and CVSS sort, /cves live search sort,
    # dashboard KEV counts) but had no supporting index, forcing a
    # sequential scan of the entire cves table on every such query.
    ("index cves(kev) partial",   "CREATE INDEX IF NOT EXISTS idx_cves_kev ON cves(kev) WHERE kev = TRUE"),
    ("index cves(cvss)",          "CREATE INDEX IF NOT EXISTS idx_cves_cvss ON cves(cvss DESC)"),

    # ── Back-fill data ────────────────────────────────────────────────────────
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

    # ── Phase 6: AI Security Copilot — persistent conversations ───────────────
    # NOTE: an earlier ad-hoc setup created ai_conversations/ai_messages with a
    # different shape (user_id INTEGER instead of username TEXT, no
    # updated_at/archived/tokens columns). CREATE TABLE IF NOT EXISTS silently
    # no-ops against that pre-existing table, so the ALTER TABLE ... ADD COLUMN
    # IF NOT EXISTS statements below are what actually repair a live database —
    # they are required even though the CREATE TABLE above also defines them.
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
     # username must be nullable here (no DEFAULT can backfill a real
     # username for legacy rows); the application always supplies one on
     # INSERT, so existing/legacy rows are the only ones that could be
     # NULL, and there are none in this deployment.
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
    # was first defined, CREATE TABLE IF NOT EXISTS silently skipped that FK
    # along with everything else. Without it, deleting a conversation
    # (database.conversations.delete_conversation) never cascades to its
    # messages — they become permanently orphaned, referencing a
    # conversation_id that no longer exists in ai_conversations. This is
    # exactly the corruption pattern observed in production (ai_messages
    # rows for conversation_id 1 and 2 with zero matching ai_conversations
    # rows). Two steps: delete the existing orphans (Postgres refuses to
    # add a FK while violating rows exist), then add the FK for real.
    ("delete orphaned ai_messages with no matching conversation",
     """
     DELETE FROM ai_messages
     WHERE conversation_id NOT IN (SELECT id FROM ai_conversations)
     """),
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

    # ── Phase 6: AI Security Copilot — CVE analysis cache ──────────────────────
    # Same situation: an earlier minimal cve_ai_analysis table already exists
    # in production with only (cve_id, summary, explanation, guidance,
    # attack_scenario, business_impact, analyzed_at, model_used) — missing
    # technical_impact, recommended_actions, description_hash, status,
    # retry_count, error_message, created_at, updated_at. Repaired below.
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
    # The status CHECK constraint is added separately (and defensively) because
    # ADD COLUMN with an inline CHECK fails on tables that already have rows
    # violating it; this guards the same way even though there are no rows yet.
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

    # ── Phase 6 Requirement 5: trend analysis ("how does this week compare
    # to last week") needs a historical record, since `matches` only holds
    # current state — there was previously no way to answer that question
    # at all. risk_snapshots stores one row per day with aggregate counts,
    # written by a daily APScheduler job (see jobs/daily_scan.py). Storing
    # pre-aggregated daily totals (not a full table dump) keeps this table
    # small forever — one row/day means ~365 rows/year, trivial to query.
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

    # ── Phase 6 Requirement 8: chat response cache ─────────────────────────────
    # Keyed on a hash of (normalized question text + ARGUS context), not just
    # the raw question, so the cache auto-invalidates whenever the underlying
    # ARGUS data changes (new scan results, new AI analysis, etc.) without
    # needing any manual cache-busting logic. A short TTL is still enforced
    # as a safety net against staleness even when the hash happens to repeat.
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

    # ── City Exposure Overview feature ─────────────────────────────────────────
    # Nullable on purpose: existing assets have no city/country data and must
    # keep working unmodified (the feature spec explicitly requires this —
    # "do not force old records to be updated immediately"). NULL/blank city
    # is the documented "unassigned asset" case throughout this feature, not
    # an error state.
    ("assets.city column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS city VARCHAR(120)"),
    ("assets.country_code column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS country_code CHAR(2)"),
    ("index assets(country_code, city)",
     "CREATE INDEX IF NOT EXISTS idx_assets_city_country ON assets (country_code, city)"),

    # ── Asset metadata: exposure & network function ─────────────────────────────
    # exposure defaults to 'Internal' rather than NULL — every asset has a
    # real exposure state whether or not it's been reviewed yet, and
    # 'Internal' is the safer default to pre-select (an admin has to
    # actively mark something 'External', rather than an unreviewed asset
    # silently reading as external-safe). function stays nullable — unlike
    # exposure, "unclassified network role" is a legitimate, common state
    # (many assets genuinely don't fit Gateway/Endpoint/etc.) and forcing a
    # default here would misrepresent asset data rather than just being
    # conservative about it.
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
     END $$;
     """),
    ("assets.function column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS function TEXT"),
    ("index assets(exposure)",
     "CREATE INDEX IF NOT EXISTS idx_assets_exposure ON assets (exposure)"),
    ("index assets(function)",
     "CREATE INDEX IF NOT EXISTS idx_assets_function ON assets (function)"),

    # ── Patch planning (per-finding) ─────────────────────────────────────────────
    # Deliberately separate from matches.due_date: due_date is the
    # auto-calculated SLA compliance deadline (see database/matches.py's
    # _calc_due_date — derived purely from CVSS, not something an analyst
    # sets). planned_patch_date is the analyst's own scheduling decision —
    # when they actually intend to apply the patch, which may be before or
    # after the SLA deadline (e.g. tied to a maintenance window) and is
    # never auto-computed or overwritten by ARGUS. Both are shown
    # side-by-side rather than one replacing the other.
    ("matches.planned_patch_date column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS planned_patch_date DATE"),
    ("matches.patch_notes column",
     "ALTER TABLE matches ADD COLUMN IF NOT EXISTS patch_notes TEXT"),
    ("index matches(planned_patch_date)",
     "CREATE INDEX IF NOT EXISTS idx_matches_planned_patch_date ON matches (planned_patch_date)"),
]


def run():
    conn = get_connection()
    try:
        for name, sql in MIGRATIONS:
            print(f"  → {name} ... ", end="", flush=True)
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(sql)
                print("OK")
            except Exception as exc:
                print(f"FAILED\n    {exc}")
                conn.rollback()
    finally:
        conn.close()
    print("\nMigration complete.")


def run_ai_views():
    """
    Create or replace AI views used by context_builder.py.
    Safe to run multiple times — uses CREATE OR REPLACE VIEW.
    """
    views_sql = open(
        os.path.join(os.path.dirname(__file__), "schema.sql"),
        encoding="utf-8",
    ).read()

    conn = get_connection()
    try:
        # Extract only real CREATE OR REPLACE VIEW statements. Anchoring on
        # `VIEW\s+\w+\s+AS` (not just the bare phrase) is deliberate: a
        # previous version of this regex matched the literal text
        # "CREATE OR REPLACE VIEW" anywhere, including inside an SQL
        # comment that *mentioned* the phrase as prose (schema.sql had a
        # comment explaining a column-mismatch risk that contained those
        # exact words) — that silently produced a malformed statement
        # (comment text + the real view glued together) which failed to
        # execute, so ai_dashboard was never actually refreshed by this
        # function even though it looked successful in isolation.
        import re
        view_statements = re.findall(
            r"CREATE OR REPLACE VIEW\s+\w+\s+AS[\s\S]+?;",
            views_sql,
        )
        for stmt in view_statements:
            name = re.search(r"VIEW\s+(\w+)", stmt)
            label = name.group(1) if name else "unknown"
            print(f"  → view {label} ... ", end="", flush=True)
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(stmt)
                print("OK")
            except Exception as exc:
                print(f"FAILED\n    {exc}")
                conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    print("Argus database migration\n")
    run()
    print("\nArgus AI views\n")
    run_ai_views()
    print("\nBackfilling AI analysis queue\n")
    try:
        from database.cve_analysis import backfill_missing_analysis
        count = backfill_missing_analysis()
        print(f"  → queued {count} CVE(s) that had no analysis row yet")
    except Exception as exc:
        print(f"  → FAILED: {exc}")
