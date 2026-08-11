"""Driver earnings-statement PDF — Uber-style weekly/monthly summary.

Renders the dict produced by ``utils/driver_statement.build_statement`` into
a one-page branded PDF (bytes, no file I/O): a headline earnings figure, the
earnings breakdown, activity stats, the period's payouts, and standing notes
(0% commission, GST remittance reminder). Attached to the periodic statement
email (``driver_statement_job.py``) and served by the admin download endpoint
— both render through here so driver and admin always see the same document.

Same branded chrome as the T4A slip and the admin compliance reports
(utils/report_branding.py). Defensive on missing keys — a partial statement
renders with zeros rather than raising.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Payout rows rendered before the table is capped. Overflow is disclosed in the
# document (see the "+N more" line) — never silently dropped, since the total
# underneath always covers every payout.
_MAX_PAYOUT_ROWS = 40


def generate_driver_statement_pdf(statement: dict) -> bytes:
    """Return the statement PDF as raw bytes (starts with b'%PDF')."""
    try:
        from . import report_branding
    except ImportError:  # pragma: no cover
        from utils import report_branding  # type: ignore

    earnings = statement.get("earnings") or {}
    payouts = statement.get("payouts") or []
    period_type = str(statement.get("period_type") or "weekly")
    label = str(statement.get("period_label") or "")
    driver_name = str(statement.get("driver_name") or "Driver")
    doc_kind = {"monthly": "Monthly ", "weekly": "Weekly "}.get(period_type, "")

    pdf = report_branding.new_branded_pdf(
        title=f"{doc_kind}Earnings Statement",
        subtitle=[label, f"Driver: {driver_name}"],
    )
    pdf.set_auto_page_break(auto=True, margin=22)
    pdf.set_margins(left=15, top=15, right=15)
    W = 180

    ink = report_branding.INK_RGB
    muted = report_branding.MUTED_RGB
    header_bg = report_branding.HEADER_BG_RGB
    rule = report_branding.RULE_RGB

    def money(key: str) -> str:
        return str(earnings.get(key) or "0.00")

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

    def line_item(label_text: str, amount: str, *, bold: bool = False, note: str = "") -> None:
        pdf.set_font("Helvetica", "B" if bold else "", 9.5)
        pdf.set_text_color(*ink)
        pdf.cell(W - 40, 6.5, label_text, border=0)
        pdf.cell(40, 6.5, f"$ {amount}", border=0, align="R", ln=True)
        if note:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*muted)
            pdf.cell(W, 4.5, note, border=0, ln=True)
            pdf.set_text_color(*ink)

    # ── Headline: total earned / paid out ────────────────────────────────
    pdf.ln(2)
    half = W / 2
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*muted)
    pdf.cell(half, 5, "Total earnings this period", border=0)
    pdf.cell(half, 5, "Paid out this period", border=0, ln=True)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*ink)
    pdf.cell(half, 11, f"$ {money('total')}", border=0)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*muted)
    pdf.cell(half, 11, f"$ {statement.get('payouts_total') or '0.00'}", border=0, ln=True)

    # One era per number: when part of "paid out" predates Spinr, say so
    # right under the figure — otherwise "$0.00 earned" beside a real
    # paid-out amount reads as a bookkeeping error rather than history.
    _prev_app = str(statement.get("payouts_previous_app_total") or "0.00")
    if _prev_app not in ("0.00", "0", ""):
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*muted)
        pdf.cell(half, 4.5, "", border=0)
        pdf.cell(half, 4.5, f"includes $ {_prev_app} paid by the previous Spinr app", border=0, ln=True)
        pdf.set_text_color(*ink)

    if statement.get("previous_app_only"):
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 8.5)
        pdf.set_text_color(*muted)
        pdf.multi_cell(
            W,
            4.5,
            "All payments in this period are transfers made by the previous Spinr app, shown "
            "here for your records. They are not Spinr earnings, which is why total earnings "
            "for the period is $ 0.00.",
            border=0,
        )
        pdf.set_text_color(*ink)
    h_rule()

    # ── Earnings breakdown ───────────────────────────────────────────────
    section_heading("EARNINGS BREAKDOWN")
    line_item("Ride earnings", money("ride_earnings"), note=f"Includes $ {money('tips_included')} in tips")
    line_item("Ride incentives", money("incentives"))
    line_item("Quest & referral bonuses", money("bonuses"))
    line_item("Cancellation / no-show fees", money("cancellation_fees"))
    line_item("GST/PST collected on fares", money("tax_collected"))
    pdf.ln(1)
    pdf.set_draw_color(*rule)
    pdf.line(15, pdf.get_y(), 15 + W, pdf.get_y())
    pdf.ln(1.5)
    line_item("Total earnings", money("total"), bold=True)
    h_rule()

    # ── Activity ─────────────────────────────────────────────────────────
    section_heading("ACTIVITY")
    pdf.set_font("Helvetica", "", 9.5)
    third = W / 3
    trips = statement.get("trips") or 0
    km = statement.get("distance_km") or 0
    minutes = int(statement.get("duration_minutes") or 0)
    hours, mins = divmod(minutes, 60)
    pdf.cell(third, 6, f"Trips completed: {trips}", border=0)
    pdf.cell(third, 6, f"Distance on trips: {km} km", border=0)
    pdf.cell(third, 6, f"Time on trips: {hours}h {mins:02d}m", border=0, ln=True)
    h_rule()

    # ── Payouts ──────────────────────────────────────────────────────────
    section_heading("PAYOUTS THIS PERIOD")
    if not payouts:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*muted)
        pdf.cell(W, 6, "No payouts in this period.", border=0, ln=True)
        pdf.set_text_color(*ink)
    else:
        cols = [28, 62, 28, 22, 28, 12]  # date, type, amount, fee, net, status marker
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_fill_color(*header_bg)
        for width, head in zip(cols, ["Date", "Type", "Amount", "Fee", "Net", ""], strict=True):
            pdf.cell(width, 6.5, head, border=0, fill=True, align="R" if head in ("Amount", "Fee", "Net") else "L")
        pdf.ln()
        pdf.set_font("Helvetica", "", 8.5)
        for p in payouts[:_MAX_PAYOUT_ROWS]:
            status = str(p.get("status") or "")
            marker = "" if status in ("completed", "paid") else f"({status})"
            pdf.cell(cols[0], 6, str(p.get("date") or ""), border=0)
            pdf.cell(cols[1], 6, str(p.get("label") or "Payout"), border=0)
            pdf.cell(cols[2], 6, f"$ {p.get('amount') or '0.00'}", border=0, align="R")
            pdf.cell(cols[3], 6, f"$ {p.get('fee') or '0.00'}", border=0, align="R")
            pdf.cell(cols[4], 6, f"$ {p.get('net') or '0.00'}", border=0, align="R")
            pdf.set_font("Helvetica", "I", 7.5)
            pdf.cell(cols[5], 6, marker, border=0)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.ln()
        # Never let the row cap be silent: the total below covers EVERY payout,
        # so a truncated table without this line would look like the numbers
        # don't add up on a document a driver may file for tax.
        hidden = len(payouts) - _MAX_PAYOUT_ROWS
        if hidden > 0:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*muted)
            pdf.cell(
                W,
                5.5,
                f"+ {hidden} more payout{'' if hidden == 1 else 's'} not shown "
                f"(showing the first {_MAX_PAYOUT_ROWS} of {len(payouts)}). "
                "The total below includes all of them; the full list is in the app.",
                border=0,
                ln=True,
            )
            pdf.set_text_color(*ink)
        pdf.ln(1)
        pdf.set_draw_color(*rule)
        pdf.line(15, pdf.get_y(), 15 + W, pdf.get_y())
        pdf.ln(1.5)
        # Era subtotals whenever both kinds of money appear, so the gross
        # total reconciles at a glance instead of demanding row-by-row
        # arithmetic from the driver.
        _spinr_sub = str(statement.get("payouts_spinr_total") or "0.00")
        _prev_sub = str(statement.get("payouts_previous_app_total") or "0.00")
        if _prev_sub not in ("0.00", "0", "") and _spinr_sub not in ("0.00", "0", ""):
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*muted)
            pdf.cell(W - 40, 5.5, "Spinr payouts", border=0)
            pdf.cell(40, 5.5, f"$ {_spinr_sub}", border=0, align="R", ln=True)
            pdf.cell(W - 40, 5.5, "Previous app payouts", border=0)
            pdf.cell(40, 5.5, f"$ {_prev_sub}", border=0, align="R", ln=True)
            pdf.set_text_color(*ink)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.cell(W - 40, 6.5, "Total paid out", border=0)
        pdf.cell(40, 6.5, f"$ {statement.get('payouts_total') or '0.00'}", border=0, align="R", ln=True)
    h_rule()

    # ── Notes ────────────────────────────────────────────────────────────
    section_heading("NOTES")
    notes = [
        "Spinr takes 0% commission - you keep 100% of every fare.",
        "GST/PST collected on fares is included in your earnings and must be remitted to CRA by you as a registrant.",
        "Earnings are shown for the statement period (America/Regina time); payouts you request may settle in a later period.",
        "Keep this statement for your records. Your annual T4A summary is available in the app under Earnings.",
        "Questions? Contact driver support at support@spinr.ca.",
    ]
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*ink)
    for note in notes:
        # Anchor x explicitly: multi_cell can leave the cursor at the right
        # edge (new_x=RIGHT default), which clipped every note after the
        # first against the margin.
        pdf.set_x(15)
        pdf.multi_cell(W, 4.8, f"-  {note}", border=0)

    generated_at = statement.get("generated_at") or datetime.now(timezone.utc).isoformat()
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*muted)
    pdf.cell(W, 4, f"Generated: {str(generated_at)[:19].replace('T', ' ')} UTC", align="C", ln=True)
    pdf.set_text_color(0, 0, 0)

    # The footer helper positions at the bottom margin; with auto page break
    # still armed that jump itself triggers a break and strands the footer on
    # a blank trailing page. Disarm it — the body content is already laid out.
    pdf.set_auto_page_break(auto=False)
    # Settings-driven company identity when the builder resolved it (same
    # source as emails); falls back to the shipped constants otherwise.
    _lines = statement.get("company_lines")
    report_branding.render_branded_pdf_footer(
        pdf,
        company_lines=(tuple(_lines) if isinstance(_lines, (list, tuple)) and len(_lines) == 2 else None),
    )
    return bytes(pdf.output())
