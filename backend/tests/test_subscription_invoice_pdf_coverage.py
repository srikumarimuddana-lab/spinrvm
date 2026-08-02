"""Coverage for utils/subscription_invoice_pdf.py (A1c, Sub-tier B).

Spinr Pass subscription invoice PDF generator — Saskatchewan regulatory
receipt requirement (GST/PST/HST as separate line items, per CLAUDE.md's
Saskatchewan Regulatory tax section and the fare-receipt convention).
Had no dedicated test file; only 7.97% coverage as an incidental side
effect of admin-console tests that mock the generator entirely.

Pure PDF-rendering function (fpdf2, no I/O, no DB) — tests call
`generate_subscription_invoice_pdf()` for real and assert on the returned
bytes, exercising the tax-line-item branches (GST/PST/HST present or
zero-and-skipped) since that's the actual regulatory content, not just
cosmetic layout.

Test-only change — no application code modified.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.unit


def _base_kwargs(**overrides):
    kwargs = dict(
        invoice_number="INV-2026-000123",
        payment_date="2026-08-02",
        driver_name="Jordan Test-Driver",
        driver_email="jordan@example.com",
        plan_name="Spinr Pass Plus",
        duration_label="Monthly",
        billing_reason="subscription_cycle",
        subtotal=Decimal("19.99"),
        gst_amount=Decimal("1.00"),
        pst_amount=Decimal("1.20"),
        hst_amount=Decimal("0"),
        tax_total=Decimal("2.20"),
        total=Decimal("22.19"),
        province="SK",
        stripe_invoice_url=None,
    )
    kwargs.update(overrides)
    return kwargs


class TestGenerateSubscriptionInvoicePdf:
    def test_returns_valid_pdf_bytes(self):
        from backend.utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

        result = generate_subscription_invoice_pdf(**_base_kwargs())
        assert isinstance(result, bytes)
        assert result.startswith(b"%PDF")

    def test_gst_and_pst_both_present(self):
        from backend.utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

        result = generate_subscription_invoice_pdf(
            **_base_kwargs(gst_amount=Decimal("1.00"), pst_amount=Decimal("1.20"), hst_amount=Decimal("0"))
        )
        assert result.startswith(b"%PDF")

    def test_hst_province_only_hst_line(self):
        """An HST province (e.g. ON) has hst_amount > 0 and gst/pst at 0 —
        only the HST line item must render, not GST/PST."""
        from backend.utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

        result = generate_subscription_invoice_pdf(
            **_base_kwargs(
                province="ON",
                gst_amount=Decimal("0"),
                pst_amount=Decimal("0"),
                hst_amount=Decimal("2.60"),
                tax_total=Decimal("2.60"),
            )
        )
        assert result.startswith(b"%PDF")

    def test_zero_tax_skips_all_tax_line_items(self):
        """All three tax amounts at 0 (e.g. a promo-covered or tax-exempt
        charge) — the `> 0` guards must skip every tax line without error."""
        from backend.utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

        result = generate_subscription_invoice_pdf(
            **_base_kwargs(
                gst_amount=Decimal("0"),
                pst_amount=Decimal("0"),
                hst_amount=Decimal("0"),
                tax_total=Decimal("0"),
                total=Decimal("19.99"),
            )
        )
        assert result.startswith(b"%PDF")

    def test_one_time_purchase_billing_reason(self):
        """billing_reason other than 'subscription_cycle' renders as
        'One-time purchase' rather than 'Auto-renewal'."""
        from backend.utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

        result = generate_subscription_invoice_pdf(**_base_kwargs(billing_reason="manual"))
        assert result.startswith(b"%PDF")

    def test_stripe_invoice_url_present_renders_link_line(self):
        from backend.utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

        result = generate_subscription_invoice_pdf(
            **_base_kwargs(stripe_invoice_url="https://invoice.stripe.com/i/acct_123/test_abc")
        )
        assert result.startswith(b"%PDF")

    def test_stripe_invoice_url_absent_omits_link_line(self):
        from backend.utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

        result = generate_subscription_invoice_pdf(**_base_kwargs(stripe_invoice_url=None))
        assert result.startswith(b"%PDF")

    def test_empty_driver_name_falls_back_to_driver_label(self):
        from backend.utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

        result = generate_subscription_invoice_pdf(**_base_kwargs(driver_name=""))
        assert result.startswith(b"%PDF")

    def test_zero_subtotal_pct_helper_does_not_divide_by_zero(self):
        """`_pct`'s `base <= 0` guard: a zero subtotal with a nonzero tax
        amount (a degenerate but not-impossible input, e.g. a data-quality
        issue upstream) must not raise ZeroDivisionError."""
        from backend.utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

        result = generate_subscription_invoice_pdf(
            **_base_kwargs(subtotal=Decimal("0"), gst_amount=Decimal("1.00"), total=Decimal("1.00"))
        )
        assert result.startswith(b"%PDF")


class TestMoneyHelpers:
    def test_d_coerces_string_to_decimal(self):
        from backend.utils.subscription_invoice_pdf import _d

        assert _d("19.99") == Decimal("19.99")

    def test_d_none_and_empty_string_return_zero(self):
        from backend.utils.subscription_invoice_pdf import _d

        assert _d(None) == Decimal("0")
        assert _d("") == Decimal("0")

    def test_q_rounds_half_up(self):
        from backend.utils.subscription_invoice_pdf import _q

        assert _q(Decimal("19.995")) == Decimal("20.00")  # ROUND_HALF_UP, not banker's rounding
        assert _q(Decimal("19.994")) == Decimal("19.99")

    def test_fmt_renders_dollar_sign_and_two_decimals(self):
        from backend.utils.subscription_invoice_pdf import _fmt

        assert _fmt("19.9") == "$19.90"
        assert _fmt(Decimal("5")) == "$5.00"
        assert _fmt(None) == "$0.00"
