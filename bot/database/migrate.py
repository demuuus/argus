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
    ("assets.type column", "ALTER TABLE assets ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT 'Unknown'"),
    ("assets.last_scan column", "ALTER TABLE assets ADD COLUMN IF NOT EXISTS last_scan TIMESTAMPTZ"),
    ("assets.search_keyword column", "ALTER TABLE assets ADD COLUMN IF NOT EXISTS search_keyword TEXT"),
    ("cves.severity column", "ALTER TABLE cves ADD COLUMN IF NOT EXISTS severity TEXT"),
    ("cves.epss column", "ALTER TABLE cves ADD COLUMN IF NOT EXISTS epss NUMERIC(8,6)"),
    ("cves.epss_percentile column", "ALTER TABLE cves ADD COLUMN IF NOT EXISTS epss_percentile NUMERIC(8,6)"),
    ("cves.created_at column", "ALTER TABLE cves ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
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
    ("matches.first_seen column", "ALTER TABLE matches ADD COLUMN IF NOT EXISTS first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()"),

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
    ("repair ai_conversations.username column", "ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS username TEXT"),
    ("repair ai_conversations.updated_at column", "ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    ("repair ai_conversations.archived column", "ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE"),
    ("backfill ai_conversations.title default", "ALTER TABLE ai_conversations ALTER COLUMN title SET DEFAULT 'New conversation'"),
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
    ("repair ai_messages.tokens column", "ALTER TABLE ai_messages ADD COLUMN IF NOT EXISTS tokens INTEGER DEFAULT 0"),

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
    ("index ai_conversations(username)", "CREATE INDEX IF NOT EXISTS idx_ai_conversations_username ON ai_conversations(username, updated_at DESC)"),
    ("index ai_messages(conversation_id)", "CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id, created_at)"),

    # ── Phase 6: AI Security Copilot — CVE analysis cache ──────────────────────
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
    ("repair cve_ai_analysis.technical_impact column", "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS technical_impact TEXT"),
    ("repair cve_ai_analysis.recommended_actions column", "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS recommended_actions TEXT"),
    ("repair cve_ai_analysis.description_hash column", "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS description_hash TEXT"),
    ("repair cve_ai_analysis.status column", "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'"),
    ("repair cve_ai_analysis.retry_count column", "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0"),
    ("repair cve_ai_analysis.error_message column", "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS error_message TEXT"),
    ("repair cve_ai_analysis.created_at column", "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    ("repair cve_ai_analysis.updated_at column", "ALTER TABLE cve_ai_analysis ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
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
    ("index cve_ai_analysis(status)", "CREATE INDEX IF NOT EXISTS idx_cve_ai_analysis_status ON cve_ai_analysis(status)"),

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
    ("assets.city column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS city VARCHAR(120)"),
    ("assets.country_code column",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS country_code CHAR(2)"),
    ("index assets(country_code, city)",
     "CREATE INDEX IF NOT EXISTS idx_assets_city_country ON assets (country_code, city)"),

    # ── Asset metadata: exposure & network function ─────────────────────────────
    ("assets.exposure column", "ALTER TABLE assets ADD COLUMN IF NOT EXISTS exposure TEXT NOT NULL DEFAULT 'Internal'"),
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
    ("assets.function column", "ALTER TABLE assets ADD COLUMN IF NOT EXISTS function TEXT"),
    ("index assets(exposure)", "CREATE INDEX IF NOT EXISTS idx_assets_exposure ON assets (exposure)"),
    ("index assets(function)", "CREATE INDEX IF NOT EXISTS idx_assets_function ON assets (function)"),

    # ── Patch planning (per-finding) ─────────────────────────────────────────────
    ("matches.planned_patch_date column", "ALTER TABLE matches ADD COLUMN IF NOT EXISTS planned_patch_date DATE"),
    ("matches.patch_notes column", "ALTER TABLE matches ADD COLUMN IF NOT EXISTS patch_notes TEXT"),
    ("index matches(planned_patch_date)", "CREATE INDEX IF NOT EXISTS idx_matches_planned_patch_date ON matches (planned_patch_date)"),
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