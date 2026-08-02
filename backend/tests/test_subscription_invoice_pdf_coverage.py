"""Coverage-closure tests for utils/subscription_invoice_pdf.py (A1c Sub-tier B).

Existing coverage (tests/test_subscription_invoice.py) explicitly mocks this
module's ``generate_subscription_invoice_pdf`` out ("rendering (fpdf) lives
in subscription_invoice_pdf.py and is mocked here") so the actual fpdf2
rendering code has never run. These tests call it directly with realistic
Decimal inputs shaped like the real call site
(``routes/drivers/subscriptions.py``'s ``_send_subscription_invoice_email``)
and ``utils/subscription_invoice.py::build_subscription_invoice_pdf``.

Money convention (CLAUDE.md): Decimal only, never float -- every subtotal
/ tax / total fixture below is a Decimal.
"""

from decimal import Decimal

from utils.subscription_invoice_pdf import (
    _d,
    _fmt,
    _q,
    generate_subscription_invoice_pdf,
)


def _kwargs(**overrides) -> dict:
    base = dict(
        invoice_number="INV-2026-000123",
        payment_date="2026-08-01",
        driver_name="Jordan Smith",
        driver_email="jordan.smith@example.com",
        plan_name="Pro",
        duration_label="Monthly",
        billing_reason="subscription_cycle",
        subtotal=Decimal("29.99"),
        gst_amount=Decimal("1.50"),
        pst_amount=Decimal("1.80"),
        hst_amount=Decimal("0.00"),
        tax_total=Decimal("3.30"),
        total=Decimal("33.29"),
        province="SK",
        stripe_invoice_url=None,
    )
    base.update(overrides)
    return base


class TestGenerateSubscriptionInvoicePdfHappyPaths:
    def test_saskatchewan_gst_plus_pst(self):
        """SK: both GST and PST line items rendered, HST line skipped."""
        pdf_bytes = generate_subscription_invoice_pdf(**_kwargs())

        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 500

    def test_hst_province_only(self):
        """An HST province: GST/PST both zero, HST line item rendered."""
        pdf_bytes = generate_subscription_invoice_pdf(
            **_kwargs(
                gst_amount=Decimal("0.00"),
                pst_amount=Decimal("0.00"),
                hst_amount=Decimal("3.90"),
                tax_total=Decimal("3.90"),
                total=Decimal("33.89"),
                province="ON",
            )
        )

        assert pdf_bytes.startswith(b"%PDF")

    def test_all_tax_lines_present_simultaneously(self):
        """Hypothetical all-three-taxes case still renders (defensive --
        the layout code branches independently on each amount > 0)."""
        pdf_bytes = generate_subscription_invoice_pdf(
            **_kwargs(
                gst_amount=Decimal("1.00"),
                pst_amount=Decimal("1.00"),
                hst_amount=Decimal("1.00"),
                tax_total=Decimal("3.00"),
                total=Decimal("32.99"),
            )
        )

        assert pdf_bytes.startswith(b"%PDF")

    def test_zero_tax_no_line_items_rendered(self):
        """subtotal == total, all tax amounts zero -- exercises the `if
        gst_amount > 0` / etc. all being False, and the `_pct` guard
        (`if base <= 0: return ""`) is not hit here since subtotal > 0."""
        pdf_bytes = generate_subscription_invoice_pdf(
            **_kwargs(
                gst_amount=Decimal("0.00"),
                pst_amount=Decimal("0.00"),
                hst_amount=Decimal("0.00"),
                tax_total=Decimal("0.00"),
                total=Decimal("29.99"),
            )
        )

        assert pdf_bytes.startswith(b"%PDF")

    def test_zero_subtotal_hits_pct_guard_branch(self):
        """subtotal == 0 with a nonzero gst_amount is a degenerate/free-plan
        edge case that exercises `_pct`'s `if base <= 0: return ""` guard
        instead of dividing by zero."""
        pdf_bytes = generate_subscription_invoice_pdf(
            **_kwargs(
                subtotal=Decimal("0.00"),
                gst_amount=Decimal("0.50"),
                pst_amount=Decimal("0.00"),
                hst_amount=Decimal("0.00"),
                tax_total=Decimal("0.50"),
                total=Decimal("0.50"),
            )
        )

        assert pdf_bytes.startswith(b"%PDF")

    def test_one_time_purchase_billing_reason(self):
        """billing_reason != 'subscription_cycle' -> 'One-time purchase'
        label branch instead of 'Auto-renewal'."""
        pdf_bytes = generate_subscription_invoice_pdf(**_kwargs(billing_reason="manual_purchase"))

        assert pdf_bytes.startswith(b"%PDF")

    def test_stripe_invoice_url_present(self):
        """stripe_invoice_url truthy -> the 'View Stripe receipt' link line
        is rendered (branch otherwise skipped when None, as in the base
        fixture)."""
        pdf_bytes = generate_subscription_invoice_pdf(
            **_kwargs(stripe_invoice_url="https://invoice.stripe.com/i/acct_123/test")
        )

        assert pdf_bytes.startswith(b"%PDF")

    def test_missing_driver_name_falls_back_to_default_label(self):
        """driver_name falsy ('') -> 'Driver' fallback in the BILLED TO
        block (`driver_name or "Driver"`)."""
        pdf_bytes = generate_subscription_invoice_pdf(**_kwargs(driver_name=""))

        assert pdf_bytes.startswith(b"%PDF")


class TestMoneyHelpers:
    def test_d_coerces_string_and_none_and_empty(self):
        assert _d("12.34") == Decimal("12.34")
        assert _d(None) == Decimal("0")
        assert _d("") == Decimal("0")
        assert _d(Decimal("5")) == Decimal("5")

    def test_q_rounds_half_up_to_cents(self):
        assert _q(Decimal("1.005")) == Decimal("1.01")
        assert _q(Decimal("1.004")) == Decimal("1.00")

    def test_fmt_produces_dollar_prefixed_two_decimal_string(self):
        assert _fmt("29.9") == "$29.90"
        assert _fmt(Decimal("3.005")) == "$3.01"
        assert _fmt(None) == "$0.00"
