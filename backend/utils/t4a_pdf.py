"""T4A PDF generator – CRA Statement of Fees for Services.

Generates a CRA-style T4A tax slip for Spinr drivers as a PDF byte string.
The PDF is produced on-demand (no file I/O) and returned directly to the
caller for streaming over HTTP.

Public API
----------
generate_t4a_pdf(summary: dict) -> bytes

``summary`` is the dict returned by ``GET /drivers/t4a/{year}``, extended
with an optional ``driver_name`` key.  Any key not present is handled
gracefully with a sensible default so the function never raises on a
partial summary.

CRA box references
------------------
Box 048 – Fees for Services (non-employment income; required for drivers
          earning ≥ $500/year from a single payer)
Box 020 – Self-employment commissions (applicable only if the driver holds a
          GST/HST account, i.e. ``gst_registered=True``)

Design notes
------------
- Uses fpdf2 (pure-Python, no system deps) — imported via the standard
  dual-import pattern so the module works whether loaded as a package
  submodule or standalone.
- All monetary values are formatted to two decimal places; the summary dict
  already carries pre-rounded ``Decimal``-derived strings from the route.
- The layout targets A4 portrait (210 × 297 mm).  Margins: 15 mm all sides.
- Font: built-in Helvetica (no external font files required).
- Header/footer use the same Spinr branded chrome (logo, brand-red accent
  rule, muted grey footer with company contact info) as the other admin
  compliance reports in utils/report_branding.py, so this document doesn't
  read as a visually different product from the rest of the report suite.
  The CRA box layout below the header is left in its own fixed format —
  the *shell* is branded, the *regulatory content* is not restyled.
"""

from __future__ import annotations

from datetime import datetime, timezone


def generate_t4a_pdf(summary: dict) -> bytes:
    """Return a CRA-style T4A PDF as raw bytes (starts with b'%PDF').

    Parameters
    ----------
    summary:
        Dict with at minimum the following keys (all others are optional):
        - year (int)
        - net_earnings (str|Decimal|float) – Box 048 amount
        - total_trips (int)
        - gst_registered (bool)
        - gst_bn (str) – GST/HST Business Number, if gst_registered
        Optional:
        - driver_name (str)
        - generated_at (str ISO-8601)
        - total_earnings (str) – falls back to net_earnings
    """
    # Lazy import so the rest of the backend doesn't pay the import cost.
    try:
        from . import report_branding
    except ImportError:
        from utils import report_branding  # type: ignore

    # ── Normalise inputs ──────────────────────────────────────────────────────
    year = int(summary.get("year", datetime.now(timezone.utc).year))
    net_earnings = _fmt_money(summary.get("net_earnings") or "0.00")
    total_trips = int(summary.get("total_trips") or 0)
    gst_registered = bool(summary.get("gst_registered", False))
    gst_bn = str(summary.get("gst_bn") or "").strip()
    driver_name = str(summary.get("driver_name") or "See driver profile").strip()
    generated_at_raw = summary.get("generated_at") or datetime.now(timezone.utc).isoformat()
    # Trim microseconds for human-readable display.
    generated_at = generated_at_raw[:19].replace("T", " ") + " UTC"

    # ── PDF document setup ────────────────────────────────────────────────────
    # Shared branded header (logo, brand-red accent rule, dark title, grey
    # subtitle) — same chrome as every other admin compliance report, so a
    # T4A slip doesn't look like a different product's document. Portrait,
    # A4 — matches the original layout this function has always used.
    pdf = report_branding.new_branded_pdf(
        title="T4A - Statement of Fees for Services",
        subtitle=f"Canada Revenue Agency / Agence du revenu du Canada  —  Tax Year: {year}",
    )
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(left=15, top=15, right=15)

    W = 180  # usable width (210 - 15 - 15)

    # ── Helper closures ───────────────────────────────────────────────────────
    def h_rule(y_offset: float = 2) -> None:
        """Draw a thin horizontal rule across the usable width."""
        pdf.ln(y_offset)
        y = pdf.get_y()
        pdf.set_draw_color(160, 160, 160)
        pdf.line(15, y, 15 + W, y)
        pdf.set_draw_color(0, 0, 0)
        pdf.ln(y_offset)

    def section_heading(text: str) -> None:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(W, 7, text, border=0, fill=True, ln=True)
        pdf.ln(1)

    def label_value(label: str, value: str, bold_value: bool = False) -> None:
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(70, 6, label + ":", border=0)
        if bold_value:
            pdf.set_font("Helvetica", "B", 9)
        pdf.cell(W - 70, 6, value, border=0, ln=True)

    def box_row(box_num: str, description: str, amount: str) -> None:
        """Render a CRA box row: box number | description | amount."""
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(245, 245, 245)
        pdf.cell(20, 8, f"Box {box_num}", border=1, fill=True, align="C")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(W - 60, 8, description, border=1)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(40, 8, f"$ {amount}", border=1, align="R", ln=True)
        pdf.ln(1)

    # ═══════════════════════════════════════════════════════════════════════════
    # PAYER INFORMATION
    # ═══════════════════════════════════════════════════════════════════════════
    section_heading("PAYER INFORMATION")
    label_value("Payer / Issuer", "Spinr Technologies Inc.")
    label_value("Business Number (BN)", "See Spinr corporate tax records")
    label_value("Address", "Saskatoon, SK, Canada")
    h_rule()

    # ═══════════════════════════════════════════════════════════════════════════
    # RECIPIENT (DRIVER) INFORMATION
    # ═══════════════════════════════════════════════════════════════════════════
    section_heading("RECIPIENT INFORMATION")
    label_value("Driver Name", driver_name)
    if gst_registered and gst_bn:
        label_value("GST/HST Business Number", gst_bn)
    elif gst_registered:
        label_value("GST/HST Registration", "Registered (BN not on file)")
    else:
        label_value("GST/HST Registration", "Not registered")
    h_rule()

    # ═══════════════════════════════════════════════════════════════════════════
    # INCOME BOXES
    # ═══════════════════════════════════════════════════════════════════════════
    section_heading("REPORTED AMOUNTS")
    pdf.ln(2)

    box_row("048", "Fees for Services (ride-sharing income)", net_earnings)

    if gst_registered:
        # Box 020 – Self-employment commissions (only for GST-registered drivers).
        box_row("020", "Self-employment commissions (GST registrant)", net_earnings)

    pdf.ln(4)

    # ═══════════════════════════════════════════════════════════════════════════
    # SUPPLEMENTARY DETAILS
    # ═══════════════════════════════════════════════════════════════════════════
    section_heading("SUPPLEMENTARY DETAILS")
    label_value("Total Completed Trips", str(total_trips))
    label_value("Platform Commission Rate", "0% (zero-commission platform)")
    label_value("Net Earnings (after platform fees)", f"$ {net_earnings}", bold_value=True)
    h_rule()

    # ═══════════════════════════════════════════════════════════════════════════
    # NOTES
    # ═══════════════════════════════════════════════════════════════════════════
    section_heading("CRA REPORTING NOTES")
    notes = [
        "This T4A slip is issued if your annual fees from Spinr are $500 CAD or more.",
        "Box 048 (Fees for Services) must be reported on your T1 General Return.",
        "If GST/HST registered, report commissions as self-employment income (Box 020).",
        "GST/HST collected on fares must be remitted separately to CRA.",
        "Keep this slip for your records. Do not attach to your paper return.",
        "For questions, contact Spinr driver support at support@spinr.ca.",
    ]
    pdf.set_font("Helvetica", "", 8)
    for note in notes:
        pdf.cell(5, 5, "-", border=0)
        pdf.multi_cell(W - 5, 5, note, border=0)

    h_rule(4)

    # ═══════════════════════════════════════════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════════════════════════════════════════
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(100, 100, 100)
    footer_lines = [
        "This slip is issued by Spinr Technologies Inc. for CRA reporting purposes.",
        f"Generated: {generated_at}  |  For the tax year ending December 31, {year}",
        "This document was produced electronically and is valid without a signature.",
    ]
    for line in footer_lines:
        pdf.cell(W, 4, line, align="C", ln=True)

    # Reset colour before output.
    pdf.set_text_color(0, 0, 0)

    # Standard pinned-to-bottom company/contact line — same treatment as
    # every other Spinr compliance report, so this slip's footer reads as
    # the same document family instead of its own one-off wording.
    report_branding.render_branded_pdf_footer(pdf)

    # ── Serialise to bytes ─────────────────────────────────────────────────────
    # FPDF.output() returns bytes when no destination is provided.
    return bytes(pdf.output())


# ── Private helpers ────────────────────────────────────────────────────────────


def _fmt_money(value: object) -> str:
    """Coerce value to a two-decimal-place string, e.g. '3521.75'."""
    try:
        from decimal import Decimal as _D

        return str(_D(str(value)).quantize(_D("0.01")))
    except Exception:
        return "0.00"
