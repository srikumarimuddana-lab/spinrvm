"""
Shared Spinr Pass invoice assembly.

`generate_subscription_invoice_pdf` (utils/subscription_invoice_pdf.py) renders
the branded PDF from 14 keyword args, and `_send_subscription_invoice_email`
(routes/drivers.py) emails it. Both need the same args assembled from a
`subscription_payments` row — driver/user/plan lookups, the legacy tax fallback
(rows predating migration 186 have NULL tax columns), date formatting, duration
label, invoice number. This module is the single source of that assembly so the
admin PDF download and the admin email-resend stay byte-for-byte consistent with
the driver's own invoices.

Money stays in `Decimal` end to end (never float), per the money-arithmetic
convention.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Tuple

try:
    from .. import db_supabase  # type: ignore
except ImportError:
    import db_supabase  # type: ignore

try:
    from .subscription_invoice_pdf import generate_subscription_invoice_pdf
except ImportError:
    from subscription_invoice_pdf import generate_subscription_invoice_pdf  # type: ignore

try:
    from .spinr_pass import pass_duration_label
except ImportError:
    from spinr_pass import pass_duration_label  # type: ignore

logger = logging.getLogger(__name__)


def _q2(value) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def invoice_number_for(payment: dict) -> str:
    """Stable, human-friendly invoice id derived from the payment id."""
    return f"SPX-{str(payment.get('id', ''))[:8].upper()}"


async def _resolve_invoice_scalars(payment: dict) -> dict:
    """Assemble the cadence/tax/date scalars shared by the PDF and the email.

    Does the plan lookup (for duration), applies the legacy tax fallback, formats
    the date, and derives the invoice number. Returns a dict that is a superset
    of both `generate_subscription_invoice_pdf`'s money/label kwargs and
    `_send_subscription_invoice_email`'s kwargs.
    """
    plan = None
    if payment.get("plan_id"):
        plan = await db_supabase.find_one("subscription_plans", {"id": payment["plan_id"]})
    duration_days = (plan or {}).get("duration_days", 30)
    duration_label = pass_duration_label(duration_days)

    total = _q2(payment.get("amount"))
    # Tax columns (migration 186) are nullable — legacy rows have none, so fall
    # back to a tax-free invoice with the whole amount as the subtotal.
    if payment.get("subtotal") is not None:
        subtotal = _q2(payment["subtotal"])
        gst = _q2(payment.get("gst_amount"))
        pst = _q2(payment.get("pst_amount"))
        hst = _q2(payment.get("hst_amount"))
        tax_total = _q2(payment.get("tax_total"))
        province = payment.get("province") or "SK"
    else:
        subtotal = total
        gst = pst = hst = tax_total = Decimal("0.00")
        province = "SK"

    raw_date = payment.get("created_at") or ""
    try:
        payment_date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).strftime("%B %d, %Y")
    except (ValueError, TypeError):
        payment_date = datetime.now(timezone.utc).strftime("%B %d, %Y")

    return {
        "plan_name": payment.get("plan_name") or "Spinr Pass",
        "duration_label": duration_label,
        "billing_reason": payment.get("billing_reason") or "one_off",
        "subtotal": subtotal,
        "gst_amount": gst,
        "pst_amount": pst,
        "hst_amount": hst,
        "tax_total": tax_total,
        "total": total,
        "province": province,
        "payment_date": payment_date,
        "invoice_number": invoice_number_for(payment),
        # Stripe receipt link (migration 188); None for legacy/dev/one-off rows.
        "stripe_invoice_url": payment.get("stripe_invoice_url"),
    }


async def build_subscription_invoice_pdf(payment: dict) -> Optional[Tuple[bytes, str]]:
    """Render the Spinr Pass invoice PDF for a `subscription_payments` row.

    Returns ``(pdf_bytes, filename)``, or ``None`` when the owning driver row
    cannot be resolved (so the caller can surface a clean 404 rather than a
    half-built document).
    """
    driver_id = payment.get("driver_id")
    if not driver_id:
        return None

    driver = await db_supabase.find_one("drivers", {"id": driver_id})
    if not driver:
        return None
    user = await db_supabase.find_one("users", {"id": driver.get("user_id")}) or {}
    email = user.get("email", "") or ""
    driver_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or "Driver"

    scalars = await _resolve_invoice_scalars(payment)

    pdf_bytes = generate_subscription_invoice_pdf(
        invoice_number=scalars["invoice_number"],
        payment_date=scalars["payment_date"],
        driver_name=driver_name,
        driver_email=email,
        plan_name=scalars["plan_name"],
        duration_label=scalars["duration_label"],
        billing_reason=scalars["billing_reason"],
        subtotal=scalars["subtotal"],
        gst_amount=scalars["gst_amount"],
        pst_amount=scalars["pst_amount"],
        hst_amount=scalars["hst_amount"],
        tax_total=scalars["tax_total"],
        total=scalars["total"],
        province=scalars["province"],
        stripe_invoice_url=scalars["stripe_invoice_url"],
    )
    filename = f"SpinrPass_Invoice_{scalars['invoice_number']}.pdf"
    return pdf_bytes, filename


async def build_invoice_email_kwargs(payment: dict) -> Optional[dict]:
    """Assemble the kwargs for `_send_subscription_invoice_email` from a payment.

    Returns ``None`` when the payment has no driver_id. The email helper resolves
    the driver's name/email itself, so only driver_id + the shared scalars are
    needed here.
    """
    driver_id = payment.get("driver_id")
    if not driver_id:
        return None
    scalars = await _resolve_invoice_scalars(payment)
    return {"driver_id": driver_id, **scalars}
