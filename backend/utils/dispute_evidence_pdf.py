"""C23 item 4: renders the dispute-evidence-pack's PDF pages (invoice
summary, ride timeline + account history) using the shared branded-PDF
helpers in report_branding.py -- the same toolkit subscription invoices and
decal letters use, so the pack's pages look like every other Spinr-branded
document rather than a one-off.

Data comes from utils/dispute_evidence_pack.py's assembly functions; this
module is pure rendering (fpdf2 -> bytes), no DB access.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

try:
    from .report_branding import new_branded_pdf, render_branded_pdf_footer, render_pdf_table
except ImportError:  # pragma: no cover - direct module imports in tests
    from report_branding import new_branded_pdf, render_branded_pdf_footer, render_pdf_table  # type: ignore


def _fmt_money(cents: Any) -> str:
    try:
        amount = Decimal(str(cents or 0)) / Decimal(100)
    except Exception:
        amount = Decimal(0)
    return f"${amount:.2f}"


def render_invoice_summary_pdf(ride: Dict[str, Any], dispute: Dict[str, Any]) -> bytes:
    """One-page fare/invoice summary for the disputed ride."""
    ride_code = ride.get("ride_code") or ride.get("id") or "unknown"
    pdf = new_branded_pdf(
        f"Ride Invoice — {ride_code}",
        subtitle=[
            f"Dispute {dispute.get('stripe_dispute_id') or dispute.get('id') or ''}",
            f"Completed: {ride.get('ride_completed_at') or 'n/a'}",
        ],
    )
    rows = [
        {"line_item": "Base fare", "amount": _fmt_money(ride.get("base_fare"))},
        {"line_item": "Distance fare", "amount": _fmt_money(ride.get("distance_fare"))},
        {"line_item": "Time fare", "amount": _fmt_money(ride.get("time_fare"))},
        {"line_item": "Booking fee", "amount": _fmt_money(ride.get("booking_fee"))},
        {"line_item": "Airport fee", "amount": _fmt_money(ride.get("airport_fee"))},
        {"line_item": "Tip", "amount": _fmt_money(ride.get("tip_amount"))},
        {"line_item": "Total fare", "amount": _fmt_money(ride.get("total_fare"))},
    ]
    render_pdf_table(pdf, ["line_item", "amount"], rows, col_widths=[3, 1])
    render_branded_pdf_footer(pdf)
    return bytes(pdf.output())


def render_timeline_and_history_pdf(
    ride: Dict[str, Any],
    timeline: List[Dict[str, Any]],
    account_summary: Dict[str, Any],
) -> bytes:
    """One-page ride timeline + account-history summary."""
    ride_code = ride.get("ride_code") or ride.get("id") or "unknown"
    pdf = new_branded_pdf(f"Ride Timeline — {ride_code}", subtitle="Evidence pack: chronology + account standing")

    if timeline:
        render_pdf_table(pdf, ["event", "at"], timeline, col_widths=[2, 2])
    else:
        pdf.set_font(pdf.font_family, "", 10)
        pdf.cell(0, 8, "No timeline events recorded for this ride.", ln=True)

    pdf.ln(6)
    pdf.set_font(pdf.font_family, "B", 11)
    pdf.cell(0, 8, "Account history", ln=True)
    history_rows = [
        {"field": "Driver code", "value": account_summary.get("driver_code") or "n/a"},
        {
            "field": "Rider account since",
            "value": account_summary.get("rider_account_created_at") or "n/a",
        },
        {
            "field": "Rider completed rides",
            "value": str(account_summary.get("rider_completed_ride_count"))
            if account_summary.get("rider_completed_ride_count") is not None
            else "n/a",
        },
        {
            "field": "Rider prior dispute count",
            "value": str(account_summary.get("rider_prior_dispute_count"))
            if account_summary.get("rider_prior_dispute_count") is not None
            else "n/a",
        },
    ]
    render_pdf_table(pdf, ["field", "value"], history_rows, col_widths=[2, 2])
    render_branded_pdf_footer(pdf)
    return bytes(pdf.output())
