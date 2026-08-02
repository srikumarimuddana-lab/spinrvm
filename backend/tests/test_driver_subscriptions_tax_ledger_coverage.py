"""Coverage for routes/drivers/subscriptions.py branches not exercised by
test_spinr_pass_subscription.py (A1c, Sub-tier A — Spinr Pass is money-adjacent).

test_spinr_pass_subscription.py already covers the checkout/webhook/verify-session
activation flow, _cancel_stripe_subscription, and cancel_subscription end-to-end.
_compute_subscription_tax and _record_subscription_payment are called from those
tests only through the "no service area / no tax config" short-circuit path — the
actual tax-rate math and the ledger's duplicate-vs-real-failure branching were
never directly exercised. The driver-facing resend-invoice endpoint
(`POST /subscription/payments/{payment_id}/resend-invoice`) had zero coverage —
only its admin-console sibling (`routes/admin/subscriptions.py`, a different
endpoint) was tested.

Test-only change — no application code modified.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


# ── _compute_subscription_tax ──────────────────────────────────────────────


class TestComputeSubscriptionTax:
    async def test_no_service_area_defaults_to_zero_tax_sk(self):
        from backend.routes.drivers import _compute_subscription_tax

        with patch("backend.db_supabase.find_one", AsyncMock(return_value=None)):
            result = await _compute_subscription_tax("driver-1", Decimal("49.99"))

        assert result["subtotal"] == Decimal("49.99")
        assert result["gst_amount"] == Decimal("0")
        assert result["pst_amount"] == Decimal("0")
        assert result["hst_amount"] == Decimal("0")
        assert result["tax_total"] == Decimal("0")
        assert result["total"] == Decimal("49.99")
        assert result["province"] == "SK"

    async def test_applies_configured_gst_pst_rates(self):
        from backend.routes.drivers import _compute_subscription_tax

        driver_row = {"id": "driver-1", "service_area_id": "area-1"}
        area_row = {
            "id": "area-1",
            "subscription_tax_config": {"enabled": True, "province": "SK", "gst_rate": 5, "pst_rate": 6},
        }
        with patch(
            "backend.db_supabase.find_one",
            AsyncMock(side_effect=[driver_row, area_row]),
        ):
            result = await _compute_subscription_tax("driver-1", Decimal("100.00"))

        assert result["subtotal"] == Decimal("100.00")
        assert result["gst_amount"] == Decimal("5.00")
        assert result["pst_amount"] == Decimal("6.00")
        assert result["hst_amount"] == Decimal("0")
        assert result["tax_total"] == Decimal("11.00")
        assert result["total"] == Decimal("111.00")
        assert result["province"] == "SK"

    async def test_applies_configured_hst_rate_for_hst_province(self):
        from backend.routes.drivers import _compute_subscription_tax

        driver_row = {"id": "driver-1", "service_area_id": "area-1"}
        area_row = {
            "id": "area-1",
            "subscription_tax_config": {"enabled": True, "province": "ON", "gst_rate": 0, "pst_rate": 0, "hst_rate": 13},
        }
        with patch(
            "backend.db_supabase.find_one",
            AsyncMock(side_effect=[driver_row, area_row]),
        ):
            result = await _compute_subscription_tax("driver-1", Decimal("100.00"))

        assert result["hst_amount"] == Decimal("13.00")
        assert result["gst_amount"] == Decimal("0")
        assert result["pst_amount"] == Decimal("0")
        assert result["tax_total"] == Decimal("13.00")
        assert result["province"] == "ON"

    async def test_tax_config_disabled_skips_tax_even_with_service_area(self):
        from backend.routes.drivers import _compute_subscription_tax

        driver_row = {"id": "driver-1", "service_area_id": "area-1"}
        area_row = {"id": "area-1", "subscription_tax_config": {"enabled": False, "gst_rate": 5}}
        with patch(
            "backend.db_supabase.find_one",
            AsyncMock(side_effect=[driver_row, area_row]),
        ):
            result = await _compute_subscription_tax("driver-1", Decimal("100.00"))

        assert result["tax_total"] == Decimal("0")
        assert result["total"] == Decimal("100.00")

    async def test_missing_tax_config_defaults_to_enabled_sk_5_6(self):
        """An area row with no subscription_tax_config at all still applies
        the SK default 5% GST / 6% PST — `enabled` defaults True."""
        from backend.routes.drivers import _compute_subscription_tax

        driver_row = {"id": "driver-1", "service_area_id": "area-1"}
        area_row = {"id": "area-1"}
        with patch(
            "backend.db_supabase.find_one",
            AsyncMock(side_effect=[driver_row, area_row]),
        ):
            result = await _compute_subscription_tax("driver-1", Decimal("100.00"))

        assert result["gst_amount"] == Decimal("5.00")
        assert result["pst_amount"] == Decimal("6.00")
        assert result["province"] == "SK"


# ── _record_subscription_payment: duplicate vs real-failure branching ─────


class TestRecordSubscriptionPaymentFailureHandling:
    async def test_duplicate_insert_error_is_swallowed_quietly(self):
        from backend.routes.drivers import _record_subscription_payment

        with patch(
            "backend.db_supabase.insert_one",
            AsyncMock(side_effect=Exception("duplicate key value violates unique constraint")),
        ):
            # Must not raise — a replayed recurring charge dedupes on the
            # stripe_invoice_id unique index and is not a real failure.
            result = await _record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Pro",
                amount=49.99,
                billing_reason="recurring",
                stripe_invoice_id="in_dup1",
            )
        assert result is None

    async def test_non_duplicate_insert_error_is_swallowed_but_logged_as_error(self):
        from backend.routes.drivers import _record_subscription_payment

        with patch(
            "backend.db_supabase.insert_one",
            AsyncMock(side_effect=Exception("connection reset by peer")),
        ):
            # Never raises — the money already moved; the caller (webhook/
            # activation flow) must not be blocked by a ledger-write failure.
            result = await _record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Pro",
                amount=49.99,
                billing_reason="one_off",
            )
        assert result is None

    async def test_negative_amount_is_skipped_like_zero(self):
        from backend.routes.drivers import _record_subscription_payment

        insert_mock = AsyncMock()
        with patch("backend.db_supabase.insert_one", insert_mock):
            result = await _record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Pro",
                amount=-5,
                billing_reason="refund_adjustment",
            )
        insert_mock.assert_not_awaited()
        assert result is None

    async def test_tax_and_stripe_receipt_fields_included_when_provided(self):
        from backend.routes.drivers import _record_subscription_payment

        insert_mock = AsyncMock()
        with patch("backend.db_supabase.insert_one", insert_mock):
            row_id = await _record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Pro",
                amount=Decimal("111.00"),
                billing_reason="recurring",
                subtotal=Decimal("100.00"),
                gst_amount=Decimal("5.00"),
                pst_amount=Decimal("6.00"),
                hst_amount=Decimal("0"),
                tax_total=Decimal("11.00"),
                province="SK",
                stripe_invoice_id="in_1",
                stripe_invoice_url="https://stripe.example/invoice/in_1",
            )
        assert row_id is not None
        row = insert_mock.await_args.args[1]
        assert row["subtotal"] == "100.00"
        assert row["gst_amount"] == "5.00"
        assert row["pst_amount"] == "6.00"
        assert row["tax_total"] == "11.00"
        assert row["province"] == "SK"
        assert row["stripe_invoice_url"] == "https://stripe.example/invoice/in_1"

    async def test_tax_fields_omitted_when_not_provided(self):
        """Dev-mode / pre-migration-186 rows must not carry stray tax keys."""
        from backend.routes.drivers import _record_subscription_payment

        insert_mock = AsyncMock()
        with patch("backend.db_supabase.insert_one", insert_mock):
            await _record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Pro",
                amount=49.99,
                billing_reason="one_off",
            )
        row = insert_mock.await_args.args[1]
        assert "subtotal" not in row
        assert "gst_amount" not in row
        assert "stripe_invoice_url" not in row


# ── resend_subscription_invoice (driver-facing endpoint) ──────────────────


class TestResendSubscriptionInvoiceEndpoint:
    async def test_404_when_driver_profile_missing(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await resend_subscription_invoice("pay-1", {"id": "user-123"})
        assert exc.value.status_code == 404

    async def test_404_when_payment_belongs_to_a_different_driver(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch(
                "backend.db_supabase.find_one",
                AsyncMock(return_value={"id": "pay-1", "driver_id": "someone_elses_driver"}),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await resend_subscription_invoice("pay-1", {"id": "user-123"})
        assert exc.value.status_code == 404

    async def test_404_when_payment_row_does_not_exist(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await resend_subscription_invoice("pay-missing", {"id": "user-123"})
        assert exc.value.status_code == 404

    async def test_502_when_email_send_fails(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        payment = {
            "id": "pay-1",
            "driver_id": "driver-1",
            "plan_id": "plan-1",
            "plan_name": "Pro",
            "amount": "49.99",
            "billing_reason": "one_off",
            "created_at": "2026-07-01T00:00:00Z",
        }
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch(
                "backend.db_supabase.find_one",
                AsyncMock(side_effect=[payment, {"id": "plan-1", "duration_days": 30}]),
            ),
            patch("backend.routes.drivers.subscriptions._send_subscription_invoice_email", AsyncMock(return_value=False)),
        ):
            with pytest.raises(HTTPException) as exc:
                await resend_subscription_invoice("pay-1", {"id": "user-123"})
        assert exc.value.status_code == 502

    async def test_legacy_payment_without_tax_columns_zeroes_tax_and_resends(self):
        """A pre-migration-186 payment row has no subtotal/gst/pst/hst
        columns — must send with zeroed tax rather than fabricating amounts."""
        from backend.routes.drivers import resend_subscription_invoice

        payment = {
            "id": "pay-legacy",
            "driver_id": "driver-1",
            "plan_id": "plan-1",
            "plan_name": "Pro",
            "amount": "49.99",
            "billing_reason": "one_off",
            "created_at": "2026-01-15T12:00:00Z",
        }
        send_mock = AsyncMock(return_value=True)
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch(
                "backend.db_supabase.find_one",
                AsyncMock(side_effect=[payment, {"id": "plan-1", "duration_days": 30}]),
            ),
            patch("backend.routes.drivers.subscriptions._send_subscription_invoice_email", send_mock),
        ):
            result = await resend_subscription_invoice("pay-legacy", {"id": "user-123"})

        assert result == {"success": True}
        kwargs = send_mock.await_args.kwargs
        assert kwargs["subtotal"] == Decimal("49.99")
        assert kwargs["tax_total"] == Decimal("0")
        assert kwargs["province"] == "SK"
        assert kwargs["invoice_number"] == "SPX-PAY-LEGA"

    async def test_payment_with_tax_columns_resends_using_stored_values(self):
        from backend.routes.drivers import resend_subscription_invoice

        payment = {
            "id": "pay-new",
            "driver_id": "driver-1",
            "plan_id": "plan-1",
            "plan_name": "Pro",
            "amount": "111.00",
            "subtotal": "100.00",
            "gst_amount": "5.00",
            "pst_amount": "6.00",
            "hst_amount": "0",
            "tax_total": "11.00",
            "province": "SK",
            "billing_reason": "recurring",
            "created_at": "2026-07-01T00:00:00Z",
        }
        send_mock = AsyncMock(return_value=True)
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch(
                "backend.db_supabase.find_one",
                AsyncMock(side_effect=[payment, {"id": "plan-1", "duration_days": 30}]),
            ),
            patch("backend.routes.drivers.subscriptions._send_subscription_invoice_email", send_mock),
        ):
            result = await resend_subscription_invoice("pay-new", {"id": "user-123"})

        assert result == {"success": True}
        kwargs = send_mock.await_args.kwargs
        assert kwargs["subtotal"] == Decimal("100.00")
        assert kwargs["gst_amount"] == Decimal("5.00")
        assert kwargs["tax_total"] == Decimal("11.00")
        assert kwargs["billing_reason"] == "recurring"

    async def test_no_plan_id_still_resends_with_default_duration_label(self):
        """A payment with no linked plan_id (plan since deleted, or never
        set) must not 404 — falls back to the default duration label."""
        from backend.routes.drivers import resend_subscription_invoice

        payment = {
            "id": "pay-noplan",
            "driver_id": "driver-1",
            "plan_id": None,
            "plan_name": "Legacy Plan",
            "amount": "49.99",
            "billing_reason": "one_off",
            "created_at": "2026-07-01T00:00:00Z",
        }
        send_mock = AsyncMock(return_value=True)
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=payment)),
            patch("backend.routes.drivers.subscriptions._send_subscription_invoice_email", send_mock),
        ):
            result = await resend_subscription_invoice("pay-noplan", {"id": "user-123"})

        assert result == {"success": True}
        send_mock.assert_awaited_once()
