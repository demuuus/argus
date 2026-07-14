-- Argus Database Schema
-- Apply with: psql -U <user> -d <db> -f schema.sql

-- ─────────────────────────────────────────────
-- ASSETS
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    id          SERIAL PRIMARY KEY,
    vendor      TEXT        NOT NULL,
    product     TEXT        NOT NULL,
    version     TEXT        NOT NULL,
    type        TEXT        NOT NULL DEFAULT 'Unknown',
    location    TEXT,
    owner       TEXT,
    criticality TEXT,
    notes       TEXT,
    last_scan   TIMESTAMPTZ,
    exposure    TEXT        NOT NULL DEFAULT 'Internal' CHECK (exposure IN ('Internal', 'External')),
    function    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- CVES
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cves (
    cve_id      TEXT PRIMARY KEY,
    cvss        NUMERIC(4, 1),
    severity    TEXT,           -- LOW / MEDIUM / HIGH / CRITICAL
    kev         BOOLEAN     NOT NULL DEFAULT FALSE,
    published   DATE,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- MATCHES
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS matches (
    id          SERIAL PRIMARY KEY,
    asset_id    INTEGER     NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    cve_id      TEXT        NOT NULL REFERENCES cves(cve_id),
    risk_score  INTEGER,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    planned_patch_date DATE,
    patch_notes TEXT,
    UNIQUE (asset_id, cve_id)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_matches_asset_id ON matches(asset_id);
CREATE INDEX IF NOT EXISTS idx_matches_cve_id   ON matches(cve_id);
CREATE INDEX IF NOT EXISTS idx_matches_risk     ON matches(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_assets_type      ON assets(type);

-- ─────────────────────────────────────────────
-- ALERTS (for future historical alert storage)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id          SERIAL PRIMARY KEY,
    asset_id    INTEGER     REFERENCES assets(id) ON DELETE SET NULL,
    message     TEXT        NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- Migration helpers (idempotent column additions)
-- ─────────────────────────────────────────────
DO $$
BEGIN
    -- Add type column if upgrading from older schema
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='assets' AND column_name='type'
    ) THEN
        ALTER TABLE assets ADD COLUMN type TEXT NOT NULL DEFAULT 'Unknown';
    END IF;

    -- Add last_scan column if upgrading
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='assets' AND column_name='last_scan'
    ) THEN
        ALTER TABLE assets ADD COLUMN last_scan TIMESTAMPTZ;
    END IF;

    -- Add severity column to cves if upgrading
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='cves' AND column_name='severity'
    ) THEN
        ALTER TABLE cves ADD COLUMN severity TEXT;
    END IF;

    -- Add UNIQUE constraint to matches if upgrading
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name='matches_asset_id_cve_id_key'
        AND table_name='matches'
    ) THEN
        ALTER TABLE matches ADD CONSTRAINT matches_asset_id_cve_id_key
            UNIQUE (asset_id, cve_id);
    END IF;

    -- Create alerts table if upgrading
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name='alerts'
    ) THEN
        CREATE TABLE alerts (
            id          SERIAL PRIMARY KEY,
            asset_id    INTEGER     REFERENCES assets(id) ON DELETE SET NULL,
            message     TEXT        NOT NULL,
            sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    END IF;
END $$;
-- ─────────────────────────────────────────────
-- Phase 2: Remediation, SLA & Assignment columns
-- (idempotent — safe to run on existing databases)
-- ─────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='matches' AND column_name='status') THEN
        ALTER TABLE matches ADD COLUMN status TEXT NOT NULL DEFAULT 'Open'
            CHECK (status IN ('Open','In Progress','Resolved','Accepted Risk','False Positive'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='matches' AND column_name='patched') THEN
        ALTER TABLE matches ADD COLUMN patched BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='matches' AND column_name='resolved_at') THEN
        ALTER TABLE matches ADD COLUMN resolved_at TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='matches' AND column_name='due_date') THEN
        ALTER TABLE matches ADD COLUMN due_date DATE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='matches' AND column_name='assigned_to') THEN
        ALTER TABLE matches ADD COLUMN assigned_to TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='matches' AND column_name='assigned_team') THEN
        ALTER TABLE matches ADD COLUMN assigned_team TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='assets' AND column_name='search_keyword') THEN
        ALTER TABLE assets ADD COLUMN search_keyword TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='cves' AND column_name='epss') THEN
        ALTER TABLE cves ADD COLUMN epss NUMERIC(8,6);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='cves' AND column_name='epss_percentile') THEN
        ALTER TABLE cves ADD COLUMN epss_percentile NUMERIC(8,6);
    END IF;
END $$;

-- Phase 2 indexes
CREATE INDEX IF NOT EXISTS idx_matches_status    ON matches(status);
CREATE INDEX IF NOT EXISTS idx_matches_due_date  ON matches(due_date);
CREATE INDEX IF NOT EXISTS idx_matches_asset_cve ON matches(asset_id, status);  -- composite

-- ─────────────────────────────────────────────
-- Asset metadata (exposure/function) & patch planning
-- (idempotent — safe to run on existing databases)
-- ─────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='assets' AND column_name='exposure') THEN
        ALTER TABLE assets ADD COLUMN exposure TEXT NOT NULL DEFAULT 'Internal';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='assets' AND constraint_name='assets_exposure_check'
    ) THEN
        ALTER TABLE assets ADD CONSTRAINT assets_exposure_check
            CHECK (exposure IN ('Internal', 'External'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='assets' AND column_name='function') THEN
        ALTER TABLE assets ADD COLUMN function TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='matches' AND column_name='planned_patch_date') THEN
        ALTER TABLE matches ADD COLUMN planned_patch_date DATE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='matches' AND column_name='patch_notes') THEN
        ALTER TABLE matches ADD COLUMN patch_notes TEXT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_assets_exposure ON assets(exposure);
CREATE INDEX IF NOT EXISTS idx_assets_function ON assets(function);
CREATE INDEX IF NOT EXISTS idx_matches_planned_patch_date ON matches(planned_patch_date);

CREATE INDEX IF NOT EXISTS idx_cves_kev  ON cves(kev) WHERE kev = TRUE;
CREATE INDEX IF NOT EXISTS idx_cves_cvss ON cves(cvss DESC);

-- ─────────────────────────────────────────────
-- System tables
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reports (
    id           SERIAL PRIMARY KEY,
    report_type  VARCHAR(20),
    generated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    file_path    TEXT      NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- AI Views
-- ─────────────────────────────────────────────
CREATE OR REPLACE VIEW ai_dashboard AS
SELECT
    COUNT(*) AS total_findings,
    COUNT(CASE WHEN patched = FALSE THEN 1 ELSE NULL END) AS open_findings,
    COUNT(CASE WHEN risk_score >= 80 THEN 1 ELSE NULL END) AS high_risk_findings
FROM matches;

CREATE OR REPLACE VIEW ai_open_findings AS
SELECT
    a.id AS asset_id,
    a.vendor,
    a.product,
    a.owner,
    a.criticality,
    c.cve_id,
    c.severity,
    c.cvss,
    c.kev,
    c.epss,
    m.status,
    m.risk_score,
    m.assigned_team,
    m.due_date
FROM matches m
JOIN assets a ON a.id = m.asset_id
JOIN cves   c ON c.cve_id = m.cve_id
WHERE m.patched = FALSE;

CREATE OR REPLACE VIEW ai_asset_summary AS
SELECT
    a.id,
    a.vendor,
    a.product,
    a.version,
    a.type,
    a.owner,
    a.criticality,
    COUNT(DISTINCT m.cve_id) AS total_vulnerabilities,
    COUNT(CASE WHEN UPPER(c.severity) = 'CRITICAL' THEN 1 ELSE NULL END) AS critical_vulnerabilities,
    MAX(m.risk_score) AS highest_risk_score
FROM assets a
LEFT JOIN matches m ON a.id = m.asset_id
LEFT JOIN cves    c ON m.cve_id = c.cve_id
GROUP BY a.id, a.vendor, a.product, a.version, a.type, a.owner, a.criticality;

CREATE OR REPLACE VIEW ai_vulnerability_summary AS
SELECT
    c.cve_id,
    c.severity,
    c.cvss,
    c.kev,
    c.epss,
    COUNT(DISTINCT m.asset_id) AS affected_assets,
    MAX(m.risk_score) AS highest_risk_score
FROM cves c
LEFT JOIN matches m ON c.cve_id = m.cve_id
GROUP BY c.cve_id, c.severity, c.cvss, c.kev, c.epss;
