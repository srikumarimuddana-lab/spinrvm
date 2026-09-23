"""Cancellation-fee tax + corporate billing helpers (2026-09-23).

Covers ``services/cancellation_service.py``'s two new helpers:

* ``compute_cancellation_fee_tax`` — GST/PST/HST on a cancellation/no-show
  fee, reusing ``features.calculate_all_fees``' tax logic. Flag-gated
  (``cancellation_fee_tax_enabled``, default off) because taxability of the
  fee is a policy decision pending legal confirmation.
* ``bill_corporate_cancellation_fee`` — debits the company master wallet for
  a company_allowance ride's fee (``corporate_cancellation_fee_billing_enabled``,
  default off), and records a queryable ``corporate_cancellation_fee_unbilled``
  audit row whenever it does not bill.

See docs/change-log/2026-09-23-cancellation-fee-tax-receipt-corporate.md.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import cancellation_service as svc

SK_AREA = {"id": "area_sk", "gst_enabled": True, "gst_rate": 5.0, "pst_enabled": False}
TAX_ON = {"cancellation_fee_tax_enabled": True}


@pytest.mark.unit
class TestComputeCancellationFeeTax:
    async def test_flag_off_is_untaxed(self):
        tax, breakdown = await svc.compute_cancellation_fee_tax(Decimal("4.50"), {}, SK_AREA)
        assert tax == Decimal("0")
        assert breakdown == {}

    async def test_gst_only_area(self):
        # 4.50 * 5% = 0.225 -> HALF_UP 0.23 (same quantization as the fare path).
        tax, breakdown = await svc.compute_cancellation_fee_tax(Decimal("4.50"), TAX_ON, SK_AREA)
        assert tax == Decimal("0.23")
        assert isinstance(tax, Decimal)
        assert breakdown == {"GST": {"rate": 5.0, "amount": 0.23}}

    async def test_gst_and_pst_are_separate_lines(self):
        area = {**SK_AREA, "pst_enabled": True, "pst_rate": 6.0}
        tax, breakdown = await svc.compute_cancellation_fee_tax(Decimal("4.50"), TAX_ON, area)
        assert breakdown["GST"]["amount"] == 0.23
        assert breakdown["PST"] == {"rate": 6.0, "amount": 0.27}
        assert tax == Decimal("0.50")

    async def test_hst_area(self):
        area = {"id": "a", "hst_enabled": True, "hst_rate": 13.0}
        tax, breakdown = await svc.compute_cancellation_fee_tax(Decimal("5.00"), TAX_ON, area)
        assert tax == Decimal("0.65")
        assert list(breakdown) == ["HST"]

    async def test_area_fees_are_never_added(self):
        # Must not fetch/apply area_fees (airport/night surcharges belong to a
        # trip, not a cancellation). _area_fees=[] means zero DB reads.
        with patch("backend.features.db_supabase.get_rows", AsyncMock(side_effect=AssertionError("no DB read"))):
            tax, _ = await svc.compute_cancellation_fee_tax(Decimal("10.00"), TAX_ON, SK_AREA)
        assert tax == Decimal("0.50")

    async def test_no_area_or_zero_fee_is_untaxed(self):
        assert (await svc.compute_cancellation_fee_tax(Decimal("4.50"), TAX_ON, None))[0] == Decimal("0")
        assert (await svc.compute_cancellation_fee_tax(Decimal("0"), TAX_ON, SK_AREA))[0] == Decimal("0")


CORP_RIDE = {"id": "ride_corp_1", "payment_method": "company_allowance", "corporate_account_id": "co_1"}
BILL_ON = {"corporate_cancellation_fee_billing_enabled": True}


def _bill(settings, **kw):
    return svc.bill_corporate_cancellation_fee(
        ride=kw.get("ride", CORP_RIDE),
        ride_id="ride_corp_1",
        amount=Decimal("4.73"),
        fee_driver=Decimal("4.00"),
        settings=settings,
        actor_user_id="actor_1",
        source="cancellation_fee",
    )


@pytest.mark.unit
class TestBillCorporateCancellationFee:
    async def test_bills_master_wallet_with_ride_scoped_idempotency(self):
        adjust = AsyncMock(return_value={"transaction_id": "t1", "deduped": False})
        audit = AsyncMock()
        with (
            patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value={"id": "w1"})),
            patch.object(svc.corporate_wallet_service, "apply_adjustment", adjust),
            patch.object(svc.db_supabase, "insert_one", audit),
        ):
            outcome = await _bill(BILL_ON)
        assert outcome == "billed"
        kwargs = adjust.await_args.kwargs
        assert kwargs["wallet_id"] == "w1"
        assert kwargs["amount"] == Decimal("-4.73")
        assert kwargs["ride_id"] == "ride_corp_1"  # migration 297 dedup key
        assert kwargs["floor"] == Decimal("0")
        audit.assert_not_awaited()  # billed -> no write-off row

    async def test_replay_is_deduped(self):
        with (
            patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value={"id": "w1"})),
            patch.object(svc.corporate_wallet_service, "apply_adjustment", AsyncMock(return_value={"deduped": True})),
            patch.object(svc.db_supabase, "insert_one", AsyncMock()),
        ):
            assert await _bill(BILL_ON) == "deduped"

    @pytest.mark.parametrize(
        "settings,reason",
        [
            ({}, "billing_flag_off"),
            ({**BILL_ON, "corporate_billing_enabled": False}, "corporate_billing_disabled"),
        ],
    )
    async def test_gated_off_records_writeoff_and_moves_no_money(self, settings, reason):
        adjust = AsyncMock()
        audit = AsyncMock()
        with (
            patch.object(svc.corporate_wallet_service, "apply_adjustment", adjust),
            patch.object(svc.db_supabase, "insert_one", audit),
        ):
            assert await _bill(settings) == "unbilled"
        adjust.assert_not_awaited()
        row = audit.await_args.args[1]
        assert audit.await_args.args[0] == "audit_logs"
        assert row["action"] == "corporate_cancellation_fee_unbilled"
        assert row["entity_id"] == "ride_corp_1"
        assert row["details"]["reason"] == reason
        assert row["details"]["amount"] == "4.73"
        assert row["details"]["fee_driver"] == "4.00"
        assert row["details"]["company_id"] == "co_1"

    async def test_debit_failure_below_floor_records_writeoff(self):
        audit = AsyncMock()
        with (
            patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value={"id": "w1"})),
            patch.object(
                svc.corporate_wallet_service,
                "apply_adjustment",
                AsyncMock(side_effect=RuntimeError("wallet_below_floor: new=-1 floor=0")),
            ),
            patch.object(svc.db_supabase, "insert_one", audit),
        ):
            assert await _bill(BILL_ON) == "unbilled"
        assert audit.await_args.args[1]["details"]["reason"] == "debit_failed"

    async def test_no_wallet_records_writeoff(self):
        audit = AsyncMock()
        with (
            patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
            patch.object(svc.db_supabase, "insert_one", audit),
        ):
            assert await _bill(BILL_ON) == "unbilled"
        assert audit.await_args.args[1]["details"]["reason"] == "no_wallet"

    async def test_missing_company_records_writeoff(self):
        audit = AsyncMock()
        with patch.object(svc.db_supabase, "insert_one", audit):
            outcome = await _bill(BILL_ON, ride={"id": "ride_corp_1", "payment_method": "company_allowance"})
        assert outcome == "unbilled"
        assert audit.await_args.args[1]["details"]["reason"] == "no_corporate_account"

    async def test_audit_write_failure_never_raises(self):
        with patch.object(svc.db_supabase, "insert_one", AsyncMock(side_effect=RuntimeError("db down"))):
            assert await _bill({}) == "unbilled"
