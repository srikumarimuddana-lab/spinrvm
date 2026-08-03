"""Corporate billing-statement PDF invoice.

Product decision (corporate + admin portal review round 2 — "invoicing"):
a downloadable, record-only PDF per monthly statement. Renders the same
dict shape `routes/corporate_company.py::build_full_month_statement`
already computes (line items + `_aggregate_rows` summary, including the
GST/PST breakdown from round2-07/item #57) — no new aggregation logic,
this module is presentation only.

Same branded chrome and fpdf2 layout conventions as
`utils/driver_statement_pdf.py` (payout table, row cap + "+N more"
disclosure, `pdf_safe` on every dynamic string). Purely additive: this
document never changes what a company is actually charged — wallet
top-ups, subscription charges, and settlement all happen exactly as
before; this is a formatted read of numbers already computed elsewhere.
"""

from __future__ import annotations

# Line items rendered before the table is capped — total below always
# covers every line item regardless of the cap (never let the numbers on
# a document a company may file for its own books look like they don't
# add up).
_MAX_LINE_ITEM_ROWS = 40
_MAX_MEMBER_ROWS = 20


def generate_corporate_statement_pdf(company: dict, statement: dict) -> bytes:
    """Return the statement PDF as raw bytes (starts with b'%PDF')."""
    try:
        from . import report_branding
    except ImportError:  # pragma: no cover
        from utils import report_branding  # type: ignore

    pdf_safe = report_branding.pdf_safe

    summary = statement.get("summary") or {}
    line_items = statement.get("line_items") or []
    by_member = summary.get("by_member") or []
    month = str(statement.get("month") or "")
    company_name = pdf_safe(str(company.get("legal_name") or company.get("name") or "Company"))

    pdf = report_branding.new_branded_pdf(
        title="Corporate Statement",
        subtitle=[pdf_safe(f"Company: {company_name}"), f"Period: {month}"],
    )
    pdf.set_auto_page_break(auto=True, margin=22)
    pdf.set_margins(left=15, top=15, right=15)
    W = 180

    ink = report_branding.INK_RGB
    muted = report_branding.MUTED_RGB
    header_bg = report_branding.HEADER_BG_RGB
    rule = report_branding.RULE_RGB

    def money(key: str, src: dict | None = None) -> str:
        return str((src or summary).get(key) or "0.00")

    def h_rule(gap: float = 3) -> None:
        pdf.ln(gap)
        y = pdf.get_y()
        pdf.set_draw_color(*rule)
        pdf.line(15, y, 15 + W, y)
        pdf.ln(gap)

    def section_heading(text: str) -> None:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*ink)
        pdf.set_fill_color(*header_bg)
        pdf.cell(W, 7, text, border=0, fill=True, ln=True)
        pdf.ln(1.5)

    def line_item(label_text: str, amount: str, *, bold: bool = False) -> None:
        pdf.set_font("Helvetica", "B" if bold else "", 9.5)
        pdf.set_text_color(*ink)
        pdf.cell(W - 40, 6.5, label_text, border=0)
        pdf.cell(40, 6.5, f"$ {amount}", border=0, align="R", ln=True)

    # ── Headline ───────────────────────────────────────────────────────
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*muted)
    pdf.cell(W, 5, "Total spend this period", border=0, ln=True)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*ink)
    pdf.cell(W, 11, f"$ {money('total')}", border=0, ln=True)
    h_rule()

    # ── Summary ──────────────────────────────────────────────────────────
    section_heading("SUMMARY")
    line_item("Member allowance debits", money("allowance_total"))
    line_item("Company master wallet fallback", money("master_total"))
    tax_by_type = summary.get("tax_by_type") or {}
    if isinstance(tax_by_type, dict) and tax_by_type:
        for label, amount in tax_by_type.items():
            line_item(pdf_safe(str(label)), str(amount))
    else:
        line_item("Tax (GST/PST)", money("tax_total"))
    pdf.ln(1)
    pdf.set_draw_color(*rule)
    pdf.line(15, pdf.get_y(), 15 + W, pdf.get_y())
    pdf.ln(1.5)
    line_item("Total", money("total"), bold=True)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*muted)
    pdf.cell(W, 5, f"{summary.get('ride_count') or 0} rides this period", border=0, ln=True)
    pdf.set_text_color(*ink)
    h_rule()

    # ── By member ────────────────────────────────────────────────────────
    section_heading("BY MEMBER")
    if not by_member:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*muted)
        pdf.cell(W, 6, "No member activity in this period.", border=0, ln=True)
        pdf.set_text_color(*ink)
    else:
        cols = [70, 24, 28, 28, 30]  # member, rides, allowance, master, total
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_fill_color(*header_bg)
        for width, head in zip(cols, ["Member", "Rides", "Allowance", "Master", "Total"], strict=True):
            pdf.cell(width, 6.5, head, border=0, fill=True, align="R" if head != "Member" else "L")
        pdf.ln()
        pdf.set_font("Helvetica", "", 8.5)
        for m in by_member[:_MAX_MEMBER_ROWS]:
            pdf.cell(cols[0], 6, pdf_safe(str(m.get("member_id") or "unknown"))[:38], border=0)
            pdf.cell(cols[1], 6, str(m.get("ride_count") or 0), border=0, align="R")
            pdf.cell(cols[2], 6, f"$ {m.get('allowance_total') or '0.00'}", border=0, align="R")
            pdf.cell(cols[3], 6, f"$ {m.get('master_total') or '0.00'}", border=0, align="R")
            pdf.cell(cols[4], 6, f"$ {m.get('total') or '0.00'}", border=0, align="R", ln=True)
        hidden_members = len(by_member) - _MAX_MEMBER_ROWS
        if hidden_members > 0:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*muted)
            pdf.cell(
                W,
                5.5,
                f"+ {hidden_members} more member{'' if hidden_members == 1 else 's'} not shown "
                f"(showing the top {_MAX_MEMBER_ROWS} of {len(by_member)} by spend).",
                border=0,
                ln=True,
            )
            pdf.set_text_color(*ink)
    h_rule()

    # ── Line items ───────────────────────────────────────────────────────
    section_heading("RIDE LINE ITEMS")
    if not line_items:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*muted)
        pdf.cell(W, 6, "No rides in this period.", border=0, ln=True)
        pdf.set_text_color(*ink)
    else:
        cols = [22, 40, 24, 26, 26, 20, 22]  # date, member, source, allowance, master, tax, ride
        heads = ["Date", "Member", "Source", "Allowance", "Master", "Tax", "Ride"]
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(*header_bg)
        for width, head in zip(cols, heads, strict=True):
            pdf.cell(
                width,
                6.5,
                head,
                border=0,
                fill=True,
                align="R" if head in ("Allowance", "Master", "Tax") else "L",
            )
        pdf.ln()
        pdf.set_font("Helvetica", "", 7.5)
        for row in line_items[:_MAX_LINE_ITEM_ROWS]:
            created = str(row.get("created_at") or "")[:10]
            member = pdf_safe(str(row.get("member_id") or "")[:20])
            source = pdf_safe(str(row.get("source_type") or ""))
            ride_id = pdf_safe(str(row.get("ride_id") or "")[:16])
            pdf.cell(cols[0], 5.5, created, border=0)
            pdf.cell(cols[1], 5.5, member, border=0)
            pdf.cell(cols[2], 5.5, source, border=0)
            pdf.cell(cols[3], 5.5, f"$ {row.get('allowance_debit_amount') or '0.00'}", border=0, align="R")
            pdf.cell(cols[4], 5.5, f"$ {row.get('master_fallback_amount') or '0.00'}", border=0, align="R")
            pdf.cell(cols[5], 5.5, f"$ {row.get('tax_amount') or '0.00'}", border=0, align="R")
            pdf.cell(cols[6], 5.5, ride_id, border=0, ln=True)
        hidden = len(line_items) - _MAX_LINE_ITEM_ROWS
        if hidden > 0:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*muted)
            pdf.cell(
                W,
                5.5,
                f"+ {hidden} more ride{'' if hidden == 1 else 's'} not shown "
                f"(showing the first {_MAX_LINE_ITEM_ROWS} of {len(line_items)}). "
                "The totals above include all of them; the full list is in the app.",
                border=0,
                ln=True,
            )
            pdf.set_text_color(*ink)
    h_rule()

    # ── Notes ────────────────────────────────────────────────────────────
    section_heading("NOTES")
    notes = [
        "This is a record of activity, not a bill — funds were already debited from your "
        "company wallet (or subscription, where applicable) as each ride settled.",
        "GST/PST shown reflects tax already collected on rides during this period.",
        "Keep this statement for your records. The full transaction history is in the app.",
        "Questions? Contact corporate support at support@spinr.ca.",
    ]
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*ink)
    for note in notes:
        pdf.set_x(15)
        pdf.multi_cell(W, 4.8, pdf_safe(f"-  {note}"), border=0)

    from datetime import datetime, timezone

    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*muted)
    pdf.cell(
        W, 4, f"Generated: {datetime.now(timezone.utc).isoformat()[:19].replace('T', ' ')} UTC", align="C", ln=True
    )
    pdf.set_text_color(0, 0, 0)

    pdf.set_auto_page_break(auto=False)
    report_branding.render_branded_pdf_footer(pdf)
    return bytes(pdf.output())
