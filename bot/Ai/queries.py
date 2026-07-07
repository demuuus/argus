"""
queries.py — canonical SQL query strings used by context_builder.py.

All queries are parameterized and safe.
Imported by context_builder.ContextBuilder for clarity and reuse.
"""

# Top open findings ordered by risk score (most dangerous first).
# Limit is kept small to avoid LLM context explosion.
GET_OPEN_FINDINGS = """
SELECT *
FROM ai_open_findings
ORDER BY risk_score DESC
LIMIT 20;
"""

# Full dashboard aggregate stats (single row).
GET_DASHBOARD = """
SELECT *
FROM ai_dashboard
LIMIT 1;
"""

# Per-asset vulnerability summary for a specific asset.
# Caller must supply asset_id as a parameter.
GET_ASSET_SUMMARY = """
SELECT *
FROM ai_asset_summary
WHERE id = %s;
"""

# Top assets by finding count.
GET_TOP_ASSETS = """
SELECT *
FROM ai_asset_summary
ORDER BY total_findings DESC
LIMIT 10;
"""

# All findings with CISA KEV flag.
GET_KEV_FINDINGS = """
SELECT
    a.vendor, a.product, m.cve_id, m.risk_score,
    m.status, m.assigned_to, m.due_date
FROM matches m
JOIN assets a ON m.asset_id = a.id
JOIN cves   c ON m.cve_id = c.cve_id
WHERE c.kev = TRUE
ORDER BY m.risk_score DESC
LIMIT 20;
"""

# Overdue findings (due_date passed, not resolved).
GET_OVERDUE_FINDINGS = """
SELECT
    a.vendor, a.product, m.cve_id, m.risk_score,
    m.status, m.due_date, m.assigned_to
FROM matches m
JOIN assets a ON m.asset_id = a.id
WHERE m.status NOT IN ('Resolved', 'Accepted Risk', 'False Positive')
  AND m.due_date < CURRENT_DATE
ORDER BY m.risk_score DESC
LIMIT 20;
"""
