"""
Argus PDF report generator.

Produces a professionally formatted A4 PDF with:
  - Header on every page (Argus logo-text + report title)
  - Footer on every page (date + page numbers)
  - Executive summary table
  - Colour-coded findings table (KEV rows highlighted)
"""

import os
from datetime import datetime
from typing import List, Dict

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, NextPageTemplate,
    PageBreak, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

GENERATED_REPORTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "dashboard", "generated_reports",
)
os.makedirs(GENERATED_REPORTS_DIR, exist_ok=True)

# ── Brand colours ─────────────────────────────────────────────────────────────
BRAND_BLUE    = colors.HexColor("#2563eb")
BRAND_DARK    = colors.HexColor("#1a1d27")
BRAND_MUTED   = colors.HexColor("#6b7280")
BRAND_LIGHT   = colors.HexColor("#f4f6fa")
BRAND_RED     = colors.HexColor("#dc2626")
BRAND_GREEN   = colors.HexColor("#16a34a")
BRAND_BORDER  = colors.HexColor("#d8dee9")
WHITE         = colors.white

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm


def _header_footer(canvas, doc):
    """Callback that draws the header and footer on every page."""
    canvas.saveState()

    # ── Header ──────────────────────────────────────────────────────────────
    canvas.setFillColor(BRAND_BLUE)
    canvas.rect(MARGIN, PAGE_H - 14 * mm, PAGE_W - 2 * MARGIN, 1.5 * mm, fill=1, stroke=0)

    canvas.setFont("Helvetica-Bold", 14)
    canvas.setFillColor(BRAND_DARK)
    canvas.drawString(MARGIN, PAGE_H - 11 * mm, "ARGUS")

    canvas.setFont("Helvetica", 10)
    canvas.setFillColor(BRAND_MUTED)
    title_str = f"{doc.report_type.upper()} VULNERABILITY REPORT"
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 11 * mm, title_str)

    # ── Footer ──────────────────────────────────────────────────────────────
    canvas.setFillColor(BRAND_BORDER)
    canvas.rect(MARGIN, 12 * mm, PAGE_W - 2 * MARGIN, 0.4 * mm, fill=1, stroke=0)

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(BRAND_MUTED)
    canvas.drawString(MARGIN, 8 * mm, f"Generated: {doc.generation_date}")
    canvas.drawCentredString(PAGE_W / 2, 8 * mm, "Argus — Network Asset Vulnerability Manager")
    canvas.drawRightString(
        PAGE_W - MARGIN, 8 * mm,
        f"Page {doc.page} of {doc.page_count}",
    )

    canvas.restoreState()


class ArgusDoc(BaseDocTemplate):
    """BaseDocTemplate subclass that tracks page count for footer."""

    def __init__(self, filename, report_type, generation_date, **kwargs):
        super().__init__(filename, **kwargs)
        self.report_type     = report_type
        self.generation_date = generation_date
        self.page_count      = 0   # 0 = "unknown yet" (probe pass); pre-set before final pass
        self._probe_mode     = True

    def handle_pageEnd(self):
        super().handle_pageEnd()
        if self._probe_mode:
            # Probe pass: page_count starts at 0, so any pre-set value means
            # this is the final pass and must not be overwritten.
            self.page_count = self.page


def _build_doc(path, report_type, generation_date, story):
    """
    Two-pass build so the footer can show the correct total page count.
    Pass 1 renders to a throwaway buffer purely to discover the page count.
    Pass 2 renders the real file now that doc.page_count is known ahead of time.
    """
    import io
    from copy import deepcopy

    def _make_doc(target):
        d = ArgusDoc(
            target,
            report_type=report_type,
            generation_date=generation_date,
            pagesize=A4,
            topMargin=22 * mm,
            bottomMargin=22 * mm,
            leftMargin=MARGIN,
            rightMargin=MARGIN,
        )
        frame = Frame(
            MARGIN, MARGIN,
            PAGE_W - 2 * MARGIN, PAGE_H - 44 * mm,
            id="normal",
        )
        d.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_header_footer)])
        return d

    # Pass 1: discover total page count (footer not yet correct, output discarded)
    probe_buffer = io.BytesIO()
    probe_doc = _make_doc(probe_buffer)
    probe_doc.build(deepcopy(story))
    total_pages = probe_doc.page

    # Pass 2: real build, with page_count pre-set so every page's footer is correct
    final_doc = _make_doc(path)
    final_doc.page_count = total_pages
    final_doc._probe_mode = False
    final_doc.build(story)


# ── Styles ────────────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ArgTitle", parent=base["Normal"],
            fontSize=20, fontName="Helvetica-Bold",
            textColor=BRAND_DARK, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "ArgSub", parent=base["Normal"],
            fontSize=10, textColor=BRAND_MUTED, spaceAfter=12,
        ),
        "section": ParagraphStyle(
            "ArgSection", parent=base["Normal"],
            fontSize=12, fontName="Helvetica-Bold",
            textColor=BRAND_DARK, spaceBefore=14, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "ArgBody", parent=base["Normal"],
            fontSize=9, textColor=BRAND_DARK, spaceAfter=4, leading=13,
        ),
        "muted": ParagraphStyle(
            "ArgMuted", parent=base["Normal"],
            fontSize=8, textColor=BRAND_MUTED, spaceAfter=2,
        ),
    }


def _summary_table(assets, cves, kevs, findings_count):
    data = [
        ["Metric", "Value"],
        ["Total Assets Tracked",    str(assets)],
        ["Total Unique CVEs",        str(cves)],
        ["Known Exploited (KEV)",    str(kevs)],
        ["Findings in This Report",  str(findings_count)],
    ]
    t = Table(data, colWidths=[110 * mm, 60 * mm])
    t.setStyle(TableStyle([
        # Header row
        ("BACKGROUND",    (0, 0), (-1, 0),  BRAND_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  10),
        ("TOPPADDING",    (0, 0), (-1, 0),  8),
        ("BOTTOMPADDING", (0, 0), (-1, 0),  8),
        # Data rows
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BRAND_LIGHT, WHITE]),
        ("FONTSIZE",       (0, 1), (-1, -1), 9),
        ("TOPPADDING",     (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 1), (-1, -1), 5),
        ("ALIGN",          (1, 0), (1, -1),  "CENTER"),
        ("FONTNAME",       (0, 1), (0, -1),  "Helvetica"),
        ("FONTNAME",       (1, 1), (1, -1),  "Helvetica-Bold"),
        ("GRID",           (0, 0), (-1, -1), 0.5, BRAND_BORDER),
    ]))
    return t


def _findings_table(findings):
    col_headers = ["#", "Vendor", "Product", "CVE ID", "CVSS", "Severity", "Risk", "KEV"]
    col_widths  = [8*mm, 30*mm, 32*mm, 34*mm, 14*mm, 20*mm, 14*mm, 14*mm]

    table_data = [col_headers]
    for idx, row in enumerate(findings, start=1):
        kev_flag = "YES" if row.get("kev") else "—"
        table_data.append([
            str(idx),
            str(row.get("vendor") or ""),
            str(row.get("product") or ""),
            str(row.get("cve_id") or ""),
            str(row.get("cvss") if row.get("cvss") is not None else "N/A"),
            str(row.get("severity") or "N/A"),
            str(row.get("risk_score") if row.get("risk_score") is not None else "N/A"),
            kev_flag,
        ])

    t = Table(table_data, colWidths=col_widths, repeatRows=1)

    base_styles = [
        # Header
        ("BACKGROUND",    (0, 0), (-1, 0),  BRAND_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  8),
        ("TOPPADDING",    (0, 0), (-1, 0),  6),
        ("BOTTOMPADDING", (0, 0), (-1, 0),  6),
        ("ALIGN",         (0, 0), (-1, 0),  "CENTER"),
        # Data rows
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BRAND_LIGHT, WHITE]),
        ("FONTSIZE",       (0, 1), (-1, -1), 7),
        ("TOPPADDING",     (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING",  (0, 1), (-1, -1), 4),
        ("GRID",           (0, 0), (-1, -1), 0.4, BRAND_BORDER),
        ("WORDWRAP",       (0, 0), (-1, -1), True),
        ("ALIGN",          (0, 1), (0, -1),  "CENTER"),  # # column
        ("ALIGN",          (4, 1), (7, -1),  "CENTER"),  # CVSS/Severity/Risk/KEV
    ]

    # Highlight KEV rows in red
    for i, row in enumerate(table_data[1:], start=1):
        if row[-1] == "YES":
            base_styles.append(("BACKGROUND",  (7, i), (7, i), colors.HexColor("#fef2f2")))
            base_styles.append(("TEXTCOLOR",   (7, i), (7, i), BRAND_RED))
            base_styles.append(("FONTNAME",    (7, i), (7, i), "Helvetica-Bold"))

    t.setStyle(TableStyle(base_styles))
    return t


def generate_pdf(
    report_type: str,
    filename: str,
    assets: int,
    cves: int,
    kevs: int,
    findings: List[Dict],
) -> str:
    """
    Build a PDF report and return the absolute file path.

    findings list keys: vendor, product, cve_id, cvss, severity, risk_score, kev
    """
    path  = os.path.normpath(os.path.join(GENERATED_REPORTS_DIR, filename))
    now   = datetime.now()
    gen_date = now.strftime("%Y-%m-%d %H:%M UTC")
    s    = _styles()

    story = []

    # ── Cover section ──────────────────────────────────────────────────────
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"Argus {report_type.title()} Report", s["title"]))
    story.append(Paragraph(f"Period ending: {now.strftime('%B %d, %Y')}", s["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=BRAND_BORDER, spaceAfter=10))

    # ── Executive summary paragraph ────────────────────────────────────────
    story.append(Paragraph("Executive Summary", s["section"]))
    kev_pct = f"{(kevs / cves * 100):.1f}%" if cves else "0%"
    story.append(Paragraph(
        f"This {report_type.lower()} report covers <b>{assets}</b> tracked assets with a total of "
        f"<b>{cves}</b> unique CVEs identified across all assets. Of these, "
        f"<b>{kevs}</b> ({kev_pct}) are listed on CISA's Known Exploited Vulnerabilities (KEV) "
        f"catalogue and should be remediated with the highest priority. "
        f"This report lists the top <b>{len(findings)}</b> findings sorted by risk score.",
        s["body"],
    ))
    story.append(Spacer(1, 4 * mm))

    # ── Summary stats table ────────────────────────────────────────────────
    story.append(Paragraph("Summary Statistics", s["section"]))
    story.append(_summary_table(assets, cves, kevs, len(findings)))
    story.append(PageBreak())

    # ── Findings table ─────────────────────────────────────────────────────
    story.append(Paragraph("Top Findings", s["section"]))

    if not findings:
        story.append(Paragraph("No findings recorded for this period.", s["body"]))
    else:
        story.append(Paragraph(
            "The table below lists the highest-risk vulnerabilities. "
            "KEV entries are highlighted and should be treated as critical.",
            s["muted"],
        ))
        story.append(Spacer(1, 3 * mm))
        story.append(_findings_table(findings))

    # ── Disclaimer ─────────────────────────────────────────────────────────
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BRAND_BORDER))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "This report was automatically generated by Argus. CVE data is sourced from "
        "the NIST National Vulnerability Database (NVD). KEV data is sourced from CISA. "
        "Risk scores are computed using CVSS base score, EPSS probability, asset criticality, "
        "and KEV status. This document is for internal use only.",
        s["muted"],
    ))

    _build_doc(path, report_type, gen_date, story)
    return path
