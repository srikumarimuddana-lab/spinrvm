"""Tests for utils/subscription_invoice.py (shared invoice-PDF builder).

This module's job is assembling the 14 generator kwargs from a payment row
(driver/user/plan lookups + legacy tax fallback + formatting). The actual PDF
rendering (fpdf) lives in subscription_invoice_pdf.py and is mocked here so the
test validates the assembly, not the renderer.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from utils import subscription_invoice as si

_DRIVER = {"id": "drv_1", "user_id": "usr_1"}
_USER = {"id": "usr_1", "first_name": "Jordan", "last_name": "Lee", "email": "jordan@example.com"}
_PLAN = {"id": "plan_1", "duration_days": 30}


def _find_one(driver=_DRIVER, user=_USER, plan=_PLAN):
    async def _fn(table, filters):
        if table == "drivers":
            return driver
        if table == "users":
            return user
        if table == "subscription_plans":
            return plan
        return None

    return _fn


def _build(payment, **find_overrides):
    gen = MagicMock(return_value=b"%PDF-1.4 fake")
    with (
        patch.object(si.db_supabase, "find_one", new=AsyncMock(side_effect=_find_one(**find_overrides))),
        patch.object(si, "generate_subscription_invoice_pdf", gen),
    ):
        result = asyncio.run(si.build_subscription_invoice_pdf(payment))
    return result, gen


def test_assembles_kwargs_with_tax_breakdown():
    payment = {
        "id": "pay_abcdef12",
        "driver_id": "drv_1",
        "plan_id": "plan_1",
        "plan_name": "Spinr Pass 30-Day",
        "amount": "23.65",
        "subtotal": "20.00",
        "gst_amount": "1.00",
        "pst_amount": "1.20",
        "hst_amount": "0.00",
        "tax_total": "2.20",
        "province": "SK",
        "billing_reason": "subscription_cycle",
        "created_at": "2026-06-01T12:00:00Z",
        "stripe_invoice_url": "https://invoice.stripe.com/i/acct_x/test",
    }
    result, gen = _build(payment)
    assert result is not None
    pdf_bytes, filename = result
    assert pdf_bytes == b"%PDF-1.4 fake"
    # invoice number = first 8 chars of payment id, uppercased.
    assert filename == "SpinrPass_Invoice_SPX-PAY_ABCD.pdf"

    kw = gen.call_args.kwargs
    assert kw["invoice_number"] == "SPX-PAY_ABCD"
    assert kw["driver_name"] == "Jordan Lee"
    assert kw["driver_email"] == "jordan@example.com"
    assert kw["plan_name"] == "Spinr Pass 30-Day"
    assert kw["duration_label"] == "30-day"
    assert kw["billing_reason"] == "subscription_cycle"
    assert kw["subtotal"] == Decimal("20.00")
    assert kw["gst_amount"] == Decimal("1.00")
    assert kw["pst_amount"] == Decimal("1.20")
    assert kw["tax_total"] == Decimal("2.20")
    assert kw["total"] == Decimal("23.65")
    assert kw["province"] == "SK"
    assert kw["payment_date"] == "June 01, 2026"
    assert kw["stripe_invoice_url"] == "https://invoice.stripe.com/i/acct_x/test"
    # Money must be Decimal, never float.
    assert all(
        isinstance(kw[k], Decimal) for k in ("subtotal", "gst_amount", "pst_amount", "hst_amount", "tax_total", "total")
    )


def test_legacy_row_without_tax_falls_back_to_taxfree():
    payment = {
        "id": "pay_legacy0",
        "driver_id": "drv_1",
        "amount": "15.00",
        "created_at": "2026-05-01T00:00:00Z",
    }
    result, gen = _build(payment)
    assert result is not None
    kw = gen.call_args.kwargs
    assert kw["subtotal"] == Decimal("15.00")  # whole amount becomes subtotal
    assert kw["gst_amount"] == Decimal("0.00")
    assert kw["pst_amount"] == Decimal("0.00")
    assert kw["tax_total"] == Decimal("0.00")
    assert kw["total"] == Decimal("15.00")
    assert kw["province"] == "SK"
    assert kw["plan_name"] == "Spinr Pass"  # default when missing
    assert kw["stripe_invoice_url"] is None  # legacy row carries no URL


def test_returns_none_when_driver_missing():
    result, _gen = _build({"id": "pay_x", "driver_id": "drv_gone", "amount": "10.00"}, driver=None)
    assert result is None


def test_returns_none_when_no_driver_id():
    result, _gen = _build({"id": "pay_x", "amount": "10.00"})
    assert result is None
