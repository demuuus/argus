"""
context_builder.py — ARGUS AI context assembly.

Builds structured text context from PostgreSQL data to pass to the LLM.
Uses the existing get_connection() database layer — no separate db wrapper needed.

AI views required (create with migrate.py or schema.sql):
    ai_dashboard         — aggregate dashboard stats
    ai_open_findings     — open findings enriched with asset + CVE data
    ai_asset_summary     — per-asset vulnerability summary
    ai_vulnerability_summary — per-CVE summary across all assets
"""

import logging
import re
from typing import Optional

from database.db import get_connection

logger = logging.getLogger(__name__)

# ── Valid intent routing keys ─────────────────────────────────────────────────
_INTENTS = ("cve", "prioritize", "trend", "dashboard", "findings", "asset", "kev", "overdue", "team", "general")

# ── Context size limits ───────────────────────────────────────────────────────
_MAX_FINDINGS    = 20   # rows returned for findings context
_MAX_ASSET_ROWS  = 10   # rows for asset summaries

# Matches CVE-YYYY-NNNN (4+ digit sequence number, per the official CVE spec).
# Case-insensitive since users type "cve-2019-16920" as often as "CVE-2019-16920".
_CVE_ID_PATTERN = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)

class ContextBuilder:
    """
    Assembles human-readable context strings from the ARGUS database.

    Usage:
        cb = ContextBuilder()
        context = cb.build_context("Which assets have KEV vulnerabilities?")
    """

    def __init__(self) -> None:
        # No stored connection — each method opens and closes its own connection
        # to keep behaviour consistent with the rest of the codebase.
        pass

    # ── Intent routing ────────────────────────────────────────────────────────

    @staticmethod
    def determine_intent(question: str) -> str:
        """
        Map a natural-language question to one of the known intent keys.
        Returns 'general' as the fallback so callers always get a string.
        """
        if not question:
            return "general"

        q = question.lower()

        # Checked first: these phrasings would otherwise be misrouted to
        # "findings" (matches "vulnerab"/"finding") or "general" (matches nothing).
        if any(w in q for w in (
            "prioriti", "fix first", "should i fix", "what should i patch",
            "most urgent", "tackle first",
        )):
            return "prioritize"

        if any(w in q for w in (
            "compare", "trend", "this week", "last week", "vs last",
            "week over week", "getting better", "getting worse", "improving",
        )):
            return "trend"

        # "summarize" deliberately checked before "findings" below — without
        # this ordering, "summarize today's findings" matches "finding" first
        # and is misrouted to the plain findings list instead of a summary.
        if any(w in q for w in (
            "summary", "summarize", "overview", "dashboard", "total", "how many",
        )):
            return "dashboard"

        if any(w in q for w in ("kev", "exploit", "active exploit", "cisa")):
            return "kev"

        if any(w in q for w in ("overdue", "sla", "due date", "past due")):
            return "overdue"

        if any(w in q for w in ("team", "owner", "assigned", "who")):
            return "team"

        if any(w in q for w in ("asset", "device", "router", "server", "firewall")):
            return "asset"

        if any(w in q for w in ("finding", "vulnerab", "cve", "risk", "open", "unresolved")):
            return "findings"

        return "general"

    # ── Context builders ──────────────────────────────────────────────────────

    def build_dashboard_context(self) -> str:
        """Return a text summary of the ARGUS dashboard stats."""
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM ai_dashboard LIMIT 1")
                    row = cur.fetchone()
                    cols = [d[0] for d in cur.description]
                    data = dict(zip(cols, row)) if row else {}
            finally:
                conn.close()

            if not data:
                return "No dashboard data is available yet."

            lines = [
                "ARGUS Dashboard Summary",
                "─────────────────────────",
            ]
            for k, v in data.items():
                lines.append(f"{k.replace('_', ' ').title()}: {v}")
            return "\n".join(lines)

        except Exception as exc:
            logger.error("[context_builder] build_dashboard_context failed: %s", exc)
            return "Dashboard context is temporarily unavailable."

    def build_open_findings_context(self) -> str:
        """
        Return the top open findings ordered by risk score (descending).
        Limited to _MAX_FINDINGS rows to avoid LLM context explosion.
        """
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT *
                        FROM ai_open_findings
                        ORDER BY risk_score DESC
                        LIMIT %s
                        """,
                        (_MAX_FINDINGS,),
                    )
                    rows = cur.fetchall()
                    cols = [d[0] for d in cur.description]
            finally:
                conn.close()

            if not rows:
                return "No open findings are recorded."

            lines = [f"Top {len(rows)} Open Findings (by risk score)", "─────────────────────────"]
            for row in rows:
                data = dict(zip(cols, row))
                lines.append(
                    f"Asset: {data.get('product', '?')}  |  "
                    f"Owner: {data.get('owner', '?')}  |  "
                    f"CVE: {data.get('cve_id', '?')}  |  "
                    f"Severity: {data.get('severity', '?')}  |  "
                    f"Risk: {data.get('risk_score', '?')}  |  "
                    f"Status: {data.get('status', 'Open')}"
                )
            return "\n".join(lines)

        except Exception as exc:
            logger.error("[context_builder] build_open_findings_context failed: %s", exc)
            return "Open findings context is temporarily unavailable."

    def build_kev_context(self) -> str:
        """Return findings that are on the CISA KEV catalogue."""
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            a.vendor, a.product, m.cve_id, m.risk_score,
                            m.status, m.assigned_to, m.due_date
                        FROM matches m
                        JOIN assets a ON m.asset_id = a.id
                        JOIN cves   c ON m.cve_id = c.cve_id
                        WHERE c.kev = TRUE
                        ORDER BY m.risk_score DESC
                        LIMIT %s
                        """,
                        (_MAX_FINDINGS,),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()

            if not rows:
                return "No KEV (Known Exploited Vulnerability) findings are recorded."

            lines = [f"{len(rows)} KEV Findings", "─────────────────────────"]
            for vendor, product, cve_id, risk, status, assigned, due in rows:
                lines.append(
                    f"{vendor} {product} — {cve_id} | Risk {risk} | "
                    f"Status: {status or 'Open'} | Assigned: {assigned or '—'} | "
                    f"Due: {due or '—'}"
                )
            return "\n".join(lines)

        except Exception as exc:
            logger.error("[context_builder] build_kev_context failed: %s", exc)
            return "KEV context is temporarily unavailable."

    def build_overdue_context(self) -> str:
        """Return findings that have breached their SLA due_date."""
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            a.vendor, a.product, m.cve_id, m.risk_score,
                            m.status, m.due_date, m.assigned_to
                        FROM matches m
                        JOIN assets a ON m.asset_id = a.id
                        WHERE m.status NOT IN ('Resolved', 'Accepted Risk', 'False Positive')
                          AND m.due_date < CURRENT_DATE
                        ORDER BY m.risk_score DESC
                        LIMIT %s
                        """,
                        (_MAX_FINDINGS,),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()

            if not rows:
                return "No overdue findings detected. All SLAs are within target."

            lines = [f"{len(rows)} Overdue Findings", "─────────────────────────"]
            for vendor, product, cve_id, risk, status, due, assigned in rows:
                lines.append(
                    f"{vendor} {product} — {cve_id} | Risk {risk} | "
                    f"Due: {due} | Assigned: {assigned or '—'}"
                )
            return "\n".join(lines)

        except Exception as exc:
            logger.error("[context_builder] build_overdue_context failed: %s", exc)
            return "Overdue context is temporarily unavailable."

    def build_team_context(self) -> str:
        """Return ownership and assignment summary grouped by team."""
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            COALESCE(m.assigned_team, 'Unassigned') AS team,
                            COUNT(*)                                AS total,
                            SUM(CASE WHEN m.status='Open' THEN 1 ELSE 0 END) AS open_count,
                            MAX(m.risk_score)                       AS max_risk
                        FROM matches m
                        GROUP BY COALESCE(m.assigned_team, 'Unassigned')
                        ORDER BY open_count DESC
                        """
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()

            if not rows:
                return "No team assignment data available."

            lines = ["Findings by Team", "─────────────────────────"]
            for team, total, open_c, max_r in rows:
                lines.append(
                    f"Team: {team} | Total findings: {total} | "
                    f"Open: {open_c} | Max risk: {max_r}"
                )
            return "\n".join(lines)

        except Exception as exc:
            logger.error("[context_builder] build_team_context failed: %s", exc)
            return "Team context is temporarily unavailable."

    def build_asset_context(self) -> str:
        """Return a per-asset vulnerability summary."""
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT *
                        FROM ai_asset_summary
                        ORDER BY total_vulnerabilities DESC
                        LIMIT %s
                        """,
                        (_MAX_ASSET_ROWS,),
                    )
                    rows = cur.fetchall()
                    cols = [d[0] for d in cur.description]
            finally:
                conn.close()

            if not rows:
                return "No asset summary data is available."

            lines = [f"Top {len(rows)} Assets by Finding Count", "─────────────────────────"]
            for row in rows:
                data = dict(zip(cols, row))
                lines.append(
                    f"{data.get('vendor','?')} {data.get('product','?')} — "
                    f"Findings: {data.get('total_vulnerabilities','?')} | "
                    f"Critical: {data.get('critical_vulnerabilities','?')} | "
                    f"Max Risk: {data.get('highest_risk_score','?')} | "
                    f"Owner: {data.get('owner','?')}"
                )
            return "\n".join(lines)

        except Exception as exc:
            logger.error("[context_builder] build_asset_context failed: %s", exc)
            return "Asset context is temporarily unavailable."

    def build_prioritization_context(self) -> str:
        """
        Phase 6 Requirement 5: "which vulnerability should I fix first."

        risk_score already combines CVSS×10 + EPSS_percentile×1000 +
        KEV_bonus(50) + criticality_bonus(0/10/20/30) — see risk/scoring.py
        — so ORDER BY risk_score DESC is already the mathematically correct
        priority order. What was missing is showing the *individual*
        factors (not just the final number) so the model can explain its
        reasoning the way the spec asks for, instead of citing an opaque
        score the user can't independently verify.
        """
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            a.vendor, a.product, a.criticality, a.owner,
                            m.cve_id, c.cvss, c.severity, c.kev, c.epss, c.epss_percentile,
                            m.risk_score, m.status, m.due_date
                        FROM matches m
                        JOIN assets a ON m.asset_id = a.id
                        JOIN cves   c ON m.cve_id   = c.cve_id
                        WHERE m.status NOT IN ('Resolved', 'Accepted Risk', 'False Positive')
                        ORDER BY m.risk_score DESC
                        LIMIT %s
                        """,
                        (_MAX_FINDINGS,),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()

            if not rows:
                return "No open findings to prioritize — everything is resolved or accepted."

            lines = [
                f"Top {len(rows)} Findings Ranked by Priority "
                f"(risk_score = CVSS×10 + EPSS_percentile×1000 + KEV_bonus + asset_criticality_bonus)",
                "─────────────────────────",
            ]
            for i, (vendor, product, criticality, owner, cve_id, cvss, severity,
                    kev, epss, epss_pct, risk, status, due) in enumerate(rows, start=1):
                kev_str = "ACTIVELY EXPLOITED (KEV)" if kev else "not in KEV"
                lines.append(
                    f"{i}. {cve_id} on {vendor} {product} (criticality: {criticality or 'unknown'}, "
                    f"owner: {owner or 'unassigned'}) — risk score {risk}. "
                    f"CVSS {cvss}, severity {severity}, {kev_str}, "
                    f"EPSS {epss if epss is not None else 'n/a'}"
                    + (f" (percentile {epss_pct})" if epss_pct is not None else "")
                    + f", status: {status or 'Open'}"
                    + (f", due {due}" if due else "")
                )
            return "\n".join(lines)

        except Exception as exc:
            logger.error("[context_builder] build_prioritization_context failed: %s", exc)
            return "Prioritization context is temporarily unavailable."

    def build_executive_summary_context(self) -> str:
        """
        Phase 6 Requirement 5: "summarize today's findings."

        Distinct from build_dashboard_context() (which dumps the raw
        ai_dashboard view) — this assembles a narrative-ready brief: overall
        posture, what's new today specifically, and the single highest-risk
        item, so the model can write an executive-style summary rather than
        a bare stats dump.
        """
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM ai_dashboard LIMIT 1")
                    dash_row = cur.fetchone()
                    dash_cols = [d[0] for d in cur.description]
                    dash = dict(zip(dash_cols, dash_row)) if dash_row else {}

                    cur.execute(
                        """
                        SELECT COUNT(*) FROM matches
                        WHERE first_seen >= CURRENT_DATE
                        """
                    )
                    new_today = cur.fetchone()[0]

                    cur.execute(
                        """
                        SELECT a.vendor, a.product, m.cve_id, m.risk_score, c.kev
                        FROM matches m
                        JOIN assets a ON m.asset_id = a.id
                        JOIN cves   c ON m.cve_id   = c.cve_id
                        WHERE m.status NOT IN ('Resolved', 'Accepted Risk', 'False Positive')
                        ORDER BY m.risk_score DESC
                        LIMIT 1
                        """
                    )
                    top_row = cur.fetchone()

                    # Phase 6 Requirement 8/9 follow-up fix: the AI had no
                    # way to answer "how many CVEs have you finished
                    # analyzing" because no context builder ever queried
                    # cve_ai_analysis's own status counts — confirmed via a
                    # real reported case where the model correctly (but
                    # unhelpfully) said this information wasn't available,
                    # when ARGUS's own database can answer it directly.
                    cur.execute(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE status = 'complete')   AS analyzed_count,
                            COUNT(*) FILTER (WHERE status = 'pending')    AS pending_count,
                            COUNT(*) FILTER (WHERE status = 'processing') AS processing_count,
                            COUNT(*) FILTER (WHERE status = 'failed')     AS failed_count,
                            COUNT(*)                                      AS total_count
                        FROM cve_ai_analysis
                        """
                    )
                    analysis_counts = cur.fetchone()
            finally:
                conn.close()

            lines = ["Executive Summary Context", "─────────────────────────"]
            for k, v in dash.items():
                lines.append(f"{k.replace('_', ' ').title()}: {v}")
            lines.append(f"New Findings Discovered Today: {new_today}")

            if analysis_counts:
                analyzed, pending, processing, failed, total = analysis_counts
                lines.append(
                    f"AI Analysis Progress: {analyzed} of {total} CVEs fully analyzed "
                    f"({pending} pending, {processing} in progress, {failed} failed)"
                )

            if top_row:
                vendor, product, cve_id, risk, kev = top_row
                kev_note = " (actively exploited — CISA KEV)" if kev else ""
                lines.append(
                    f"Highest Priority Open Item: {cve_id} on {vendor} {product}, "
                    f"risk score {risk}{kev_note}"
                )
            else:
                lines.append("Highest Priority Open Item: none — no open findings.")

            return "\n".join(lines)

        except Exception as exc:
            logger.error("[context_builder] build_executive_summary_context failed: %s", exc)
            return "Executive summary context is temporarily unavailable."

    def build_trend_context(self) -> str:
        """
        Phase 6 Requirement 5: "how does this week compare to last week."

        Reads database/risk_snapshots.py's week-over-week comparison.
        Explicitly tells the model when fewer than 2 snapshots exist yet
        (new deployment, daily job hasn't run twice) rather than letting
        it guess or fabricate a trend that can't actually be computed —
        this is a Requirement 6 (knowledge cutoff / no hallucination)
        concern as much as a Requirement 5 one.
        """
        try:
            from database.risk_snapshots import get_week_over_week_comparison
            comparison = get_week_over_week_comparison()
        except Exception as exc:
            logger.error("[context_builder] build_trend_context failed: %s", exc)
            return "Trend context is temporarily unavailable."

        if not comparison:
            return (
                "Not enough historical data to compare trends yet. ARGUS "
                "records one risk snapshot per day; at least two snapshots "
                "roughly a week apart are needed before trend comparisons "
                "are possible. Information not available in ARGUS."
            )

        latest, previous = comparison["latest"], comparison["previous"]

        def _delta(key: str) -> str:
            new_val = latest.get(key) or 0
            old_val = previous.get(key) or 0
            diff = new_val - old_val
            if diff > 0:
                return f"{new_val} (up {diff} from {old_val})"
            elif diff < 0:
                return f"{new_val} (down {abs(diff)} from {old_val})"
            return f"{new_val} (unchanged from {old_val})"

        lines = [
            f"Trend Comparison: {latest['snapshot_date']} vs {previous['snapshot_date']}",
            "─────────────────────────",
            f"Total Findings: {_delta('total_findings')}",
            f"Open Findings: {_delta('open_findings')}",
            f"Resolved Findings: {_delta('resolved_findings')}",
            f"KEV Findings: {_delta('kev_findings')}",
            f"Overdue Findings: {_delta('overdue_findings')}",
            f"Critical Findings: {_delta('critical_findings')}",
            f"Max Risk Score: {_delta('max_risk_score')}",
            f"Total Assets: {_delta('total_assets')}",
        ]
        return "\n".join(lines)

    def build_general_context(self) -> str:
        """Fallback: return a compact dashboard summary for general questions."""
        return self.build_dashboard_context()

    def build_cve_context(self, cve_id: str) -> str:
        """
        Phase 6 Requirement 4 (Asset-Aware Reasoning): when a question names
        a specific CVE, this is what makes ARGUS answer like the spec's own
        example — "this affects a critical Cisco RV340 in the embassy
        gateway network" — instead of a generic CVSS-only description.

        Pulls, in order:
            1. The CVE's own record from `cves` (CVSS/severity/KEV/EPSS/description)
            2. The cached AI analysis from `cve_ai_analysis`, if the
               automatic analysis pipeline (Requirement 3) has already
               produced one — this avoids a second LLM call answering a
               question the analyzer already answered once.
            3. Every asset actually affected by this CVE, each with its
               vendor/product/location/owner/criticality/status/risk_score —
               the part that makes the answer asset-aware rather than generic.

        Returns a single string with all of the above, or an explicit
        "not found" message if the CVE doesn't exist in ARGUS at all
        (Requirement 6: never let the model fall back to its own training
        memory for a CVE ARGUS has no record of).
        """
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT cve_id, cvss, severity, kev, epss, epss_percentile, "
                        "published, description FROM cves WHERE cve_id = %s",
                        (cve_id,),
                    )
                    cve_row = cur.fetchone()

                    if not cve_row:
                        return (
                            f"{cve_id} was not found in the ARGUS database. "
                            f"Information not available in ARGUS."
                        )

                    cur.execute(
                        """
                        SELECT
                            a.vendor, a.product, a.location, a.owner, a.criticality,
                            m.status, m.risk_score, m.due_date, m.assigned_to,
                            m.assigned_team, m.patched
                        FROM matches m
                        JOIN assets a ON m.asset_id = a.id
                        WHERE m.cve_id = %s
                        ORDER BY m.risk_score DESC
                        """,
                        (cve_id,),
                    )
                    asset_rows = cur.fetchall()
            finally:
                conn.close()

            # Try the AI analysis cache (Requirement 2/3) — if the automatic
            # pipeline has already analyzed this CVE, surface that richer
            # context instead of asking the model to redo the same work.
            cached_analysis = None
            try:
                from database.cve_analysis import get_cached_analysis
                cached_analysis = get_cached_analysis(cve_id)
                if cached_analysis and cached_analysis.get("status") != "complete":
                    cached_analysis = None  # pending/failed rows have no usable content
            except Exception as exc:
                logger.warning("[context_builder] cve_ai_analysis lookup failed: %s", exc)

            (cid, cvss, severity, kev, epss, epss_pct, published, description) = cve_row

            lines = [f"CVE Record: {cid}", "─────────────────────────"]
            lines.append(f"CVSS: {cvss if cvss is not None else 'Information not available in ARGUS.'}")
            lines.append(f"Severity: {severity or 'Information not available in ARGUS.'}")
            lines.append(f"KEV (actively exploited): {'YES' if kev else 'No'}")
            lines.append(
                f"EPSS: {epss if epss is not None else 'Information not available in ARGUS.'}"
                + (f" (percentile {epss_pct})" if epss_pct is not None else "")
            )
            lines.append(f"Published: {published or 'Information not available in ARGUS.'}")
            lines.append(f"NVD Description: {description or 'Information not available in ARGUS.'}")

            if cached_analysis:
                lines.append("")
                lines.append("AI Analysis (previously generated, from cve_ai_analysis):")
                for label, key in [
                    ("Summary", "summary"),
                    ("Explanation", "explanation"),
                    ("Guidance", "guidance"),
                    ("Attack Scenario", "attack_scenario"),
                    ("Business Impact", "business_impact"),
                    ("Technical Impact", "technical_impact"),
                    ("Recommended Actions", "recommended_actions"),
                ]:
                    val = cached_analysis.get(key)
                    if val:
                        lines.append(f"  {label}: {val}")
            else:
                # Explicit, not silent — without this line the context simply
                # omits the analysis section, and the model can fill that
                # silence with its own guess about whether analysis exists
                # (this caused a real reported case: the chatbot told the
                # user a CVE "has been analyzed" when no cve_ai_analysis
                # row existed at all). Stating the status outright closes
                # that gap, consistent with Requirement 6's "never invent,
                # say what's actually known" rule used everywhere else here.
                lines.append("")
                lines.append(
                    "AI Analysis: This CVE has NOT been analyzed by ARGUS AI yet "
                    "(no completed row exists in cve_ai_analysis). Do not claim it "
                    "has been analyzed. If asked, say analysis is pending or in "
                    "the background queue, and that no AI-generated summary, "
                    "attack scenario, or remediation guidance is available yet."
                )

            lines.append("")
            if not asset_rows:
                lines.append(
                    "Affected Assets: None of your tracked assets are currently "
                    "matched to this CVE in ARGUS."
                )
            else:
                lines.append(f"Affected Assets ({len(asset_rows)} in your ARGUS inventory):")
                for vendor, product, location, owner, criticality, status, risk, due, assigned_to, assigned_team, patched in asset_rows:
                    patched_str = "patched" if patched else "NOT patched"
                    lines.append(
                        f"  • {vendor} {product} — criticality: {criticality or 'unknown'}, "
                        f"location: {location or 'unknown'}, owner: {owner or 'unassigned'}, "
                        f"status: {status or 'Open'}, risk score: {risk}, {patched_str}"
                        + (f", assigned to {assigned_to}" if assigned_to else "")
                        + (f" (team: {assigned_team})" if assigned_team else "")
                        + (f", due {due}" if due else "")
                    )

            return "\n".join(lines)

        except Exception as exc:
            logger.error("[context_builder] build_cve_context failed for %s: %s", cve_id, exc)
            return f"CVE context for {cve_id} is temporarily unavailable."

    # ── Main entry point ──────────────────────────────────────────────────────

    def build_context(self, question: str) -> str:
        """
        Route a natural-language question to the appropriate context builder
        and return a formatted string ready for LLM injection.

        A specific CVE ID mentioned anywhere in the question always takes
        priority over keyword-based intent matching (Requirement 4) — it's
        the strongest, language-independent signal of what the user wants,
        and it's what was missing when a user asked about CVE-2019-16920 in
        Indonesian and the model had no matching context to answer from.

        Never raises — all errors are caught and a graceful message is returned.
        """
        if not question:
            return "No question provided."

        cve_match = _CVE_ID_PATTERN.search(question)
        if cve_match:
            cve_id = cve_match.group(0).upper()
            logger.debug("[context_builder] question=%r intent=cve cve_id=%s", question, cve_id)
            try:
                return self.build_cve_context(cve_id)
            except Exception as exc:
                logger.error("[context_builder] Unhandled error for cve intent: %s", exc)
                return "ARGUS context is temporarily unavailable. Please try again."

        intent = self.determine_intent(question)
        logger.debug("[context_builder] question=%r intent=%s", question, intent)

        dispatch = {
            "dashboard":   self.build_executive_summary_context,
            "prioritize":  self.build_prioritization_context,
            "trend":       self.build_trend_context,
            "findings":    self.build_open_findings_context,
            "kev":         self.build_kev_context,
            "overdue":     self.build_overdue_context,
            "team":        self.build_team_context,
            "asset":       self.build_asset_context,
            "general":     self.build_general_context,
        }

        builder = dispatch.get(intent, self.build_general_context)
        try:
            return builder()
        except Exception as exc:
            logger.error("[context_builder] Unhandled error for intent=%s: %s", intent, exc)
            return "ARGUS context is temporarily unavailable. Please try again."
