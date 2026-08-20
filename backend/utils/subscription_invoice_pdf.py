"""
Subscription invoice PDF generator (fpdf2).

Produces a branded Spinr invoice for a Spinr Pass charge showing
pre-tax subtotal, GST, PST/HST, and total — meeting Saskatchewan
regulatory receipt requirements (CLAUDE.md).

PIPEDA: contains driver name + email (driver's own document), no rider PII.
Decimal-only money math (CLAUDE.md): never float.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

try:
    from .company_details import to_latin1
except ImportError:  # pragma: no cover - direct module imports in tests
    from utils.company_details import to_latin1  # type: ignore

_CENT = Decimal("0.01")
_BRAND = (238, 43, 43)  # #ee2b2b
_GRAY = (150, 150, 150)
_DARK = (30, 30, 30)


def _d(v: Any) -> Decimal:
    if v in (None, ""):
        return Decimal("0")
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return v.quantize(_CENT, rounding=ROUND_HALF_UP)


def _fmt(v: Any) -> str:
    return f"${_q(_d(v)):.2f}"


def generate_subscription_invoice_pdf(
    *,
    invoice_number: str,
    payment_date: str,
    driver_name: str,
    driver_email: str,
    plan_name: str,
    duration_label: str,
    billing_reason: str,
    subtotal: Decimal,
    gst_amount: Decimal,
    pst_amount: Decimal,
    hst_amount: Decimal,
    tax_total: Decimal,
    total: Decimal,
    province: str = "SK",
    stripe_invoice_url: Optional[str] = None,
    company: Optional[Any] = None,
) -> bytes:
    """Return a branded Spinr Pass invoice PDF as raw bytes (starts with b'%PDF').

    ``company`` carries the identity resolved from the admin Settings page,
    threaded in because fpdf2 is synchronous and the settings read is not. None
    keeps the pre-retrofit lines. An invoice is a tax document a driver may
    file, so the issuing company's name has to be the configured one.
    """
    from fpdf import FPDF  # type: ignore[import-untyped]

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    left, W = 15, 180

    # ── Header band ───────────────────────────────────────────────────────────
    pdf.set_fill_color(*_BRAND)
    pdf.rect(0, 0, 210, 30, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(left, 7)
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(0, 10, to_latin1(company.name if company is not None else "Spinr"), ln=True)
    pdf.set_x(left)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, "Subscription Invoice", ln=True)

    # Company detail (top-right)
    pdf.set_xy(110, 7)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(230, 230, 230)
    pdf.cell(85, 5, to_latin1(company.name if company is not None else "Spinr Mobility Inc."), align="R", ln=True)
    pdf.set_x(110)
    pdf.cell(
        85,
        5,
        to_latin1(company.contact_line if company is not None else "support@spinr.ca · www.spinr.ca"),
        align="R",
        ln=True,
    )
    pdf.set_x(110)
    # Mailing address from the Settings page; the previous hardcoded line is
    # the fallback when none is configured.
    pdf.cell(
        85,
        5,
        to_latin1((company.address if company is not None else "") or "Saskatoon, SK, Canada"),
        align="R",
        ln=True,
    )

    pdf.set_y(38)
    pdf.set_text_color(0, 0, 0)

    # ── Invoice meta ──────────────────────────────────────────────────────────
    def _meta_row(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_GRAY)
        pdf.cell(50, 6, label.upper(), border=0)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*_DARK)
        pdf.cell(0, 6, value, border=0, ln=True)

    _meta_row("Invoice #", invoice_number)
    _meta_row("Invoice Date", payment_date)
    _meta_row("Billing", "Auto-renewal" if billing_reason == "subscription_cycle" else "One-time purchase")
    pdf.ln(4)

    # ── Bill-to block ─────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_GRAY)
    pdf.cell(0, 5, "BILLED TO", ln=True)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_DARK)
    pdf.cell(0, 6, driver_name or "Driver", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*_GRAY)
    pdf.cell(0, 5, driver_email, ln=True)
    pdf.ln(6)

    # ── Total callout ─────────────────────────────────────────────────────────
    pdf.set_fill_color(248, 248, 248)
    pdf.rect(left, pdf.get_y(), W, 18, "F")
    pdf.set_xy(left + 4, pdf.get_y() + 3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_GRAY)
    pdf.cell(W * 0.5, 6, f"Spinr Pass - {plan_name} ({duration_label})", border=0)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*_BRAND)
    pdf.cell(W * 0.5 - 4, 12, _fmt(total) + " CAD", border=0, align="R")
    pdf.ln(16)
    pdf.ln(4)

    # ── Line-item table ───────────────────────────────────────────────────────
    def _header_row() -> None:
        pdf.set_fill_color(245, 245, 245)
        pdf.rect(left, pdf.get_y(), W, 8, "F")
        pdf.set_xy(left + 2, pdf.get_y() + 1)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_GRAY)
        pdf.cell(W * 0.65, 6, "DESCRIPTION", border=0)
        pdf.cell(W * 0.35 - 2, 6, "AMOUNT (CAD)", border=0, align="R")
        pdf.ln(8)

    def _item_row(label: str, amount: Decimal, bold: bool = False, color=None) -> None:
        if bold:
            pdf.set_font("Helvetica", "B", 10)
        else:
            pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*(color or _DARK))
        pdf.set_x(left + 2)
        pdf.cell(W * 0.65, 7, label, border=0)
        pdf.cell(W * 0.35 - 2, 7, _fmt(amount), border=0, align="R")
        pdf.ln(7)

    def _rule() -> None:
        y = pdf.get_y() + 1
        pdf.set_draw_color(220, 220, 220)
        pdf.line(left, y, left + W, y)
        pdf.ln(3)

    _header_row()
    _item_row(f"Spinr Pass {plan_name} - {duration_label}", subtotal)

    _rule()

    def _pct(amt: Decimal, base: Decimal) -> str:
        if base <= 0:
            return ""
        pct = (amt / base * 100).quantize(Decimal("0.01")).normalize()
        return f" ({pct}%)"

    if gst_amount > 0:
        _item_row(f"GST{_pct(gst_amount, subtotal)} - Federal", gst_amount, color=_GRAY)
    if pst_amount > 0:
        _item_row(f"PST{_pct(pst_amount, subtotal)} - {province}", pst_amount, color=_GRAY)
    if hst_amount > 0:
        _item_row(f"HST{_pct(hst_amount, subtotal)} - {province}", hst_amount, color=_GRAY)

    _rule()

    # Total row (coloured band)
    y = pdf.get_y()
    pdf.set_fill_color(*_BRAND)
    pdf.rect(left, y, W, 10, "F")
    pdf.set_xy(left + 2, y + 1)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(W * 0.65, 8, "TOTAL", border=0)
    pdf.cell(W * 0.35 - 2, 8, _fmt(total) + " CAD", border=0, align="R")
    pdf.ln(14)
    pdf.set_text_color(0, 0, 0)

    # ── Payment note ──────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_GRAY)
    pdf.cell(0, 5, "Payment successfully charged to your card on file.", ln=True)
    if stripe_invoice_url:
        pdf.set_font("Helvetica", "U", 9)
        pdf.set_text_color(*_BRAND)
        pdf.cell(0, 5, "View Stripe receipt: " + stripe_invoice_url, ln=True)
    pdf.ln(6)

    # ── Tax registration note ─────────────────────────────────────────────────
    pdf.set_draw_color(230, 230, 230)
    pdf.line(left, pdf.get_y(), left + W, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_GRAY)
    pdf.multi_cell(
        W,
        4.5,
        "This invoice is issued by Spinr Mobility Inc. in connection with the Spinr Pass subscription service. "
        "GST/PST/HST amounts are computed based on the service area province at the time of charge. "
        "Please retain this invoice for your records.",
        border=0,
    )
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, "Spinr Mobility Inc. · Saskatoon, SK · support@spinr.ca", align="C", ln=True)

    return bytes(pdf.output())
