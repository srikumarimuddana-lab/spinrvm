"""Regression tests for #4074 — corporate rides billing the rider's tip.

Product decision (2026-09-05): tips on a corporate (company_allowance) ride
stay billed to the company, matching the existing 2026-08-17 late-tip
precedent (docs/change-log/2026-08-17-wallet-corporate-late-tip-debit.md).
Only the REPORTING gap is fixed here:

  1. ``ride_payment_sources`` gains a ``tip_amount`` column (migration 407)
     so a tip is no longer invisible inside the fare on company invoices.
  2. Completion-phase corporate policy evaluation (``max_fare_per_ride``)
     uses a tip-EXCLUDED ``final_fare``, matching booking-phase evaluation's
     ``estimated_fare`` (which never includes a tip — it doesn't exist yet).
  3. ``_aggregate_rows`` (routes/corporate_company.py billing summary) and
     the PDF statement (utils/corporate_statement_pdf.py) surface a
     separate tip total/column.

Explicitly NOT changed (see the code comment above the
``_notify_allowance_threshold`` call in ``settle_corporate``): the
allowance-threshold push notification. It already reports the true
post-debit remaining balance, which is correct given tips stay
company-billed — excluding the tip there would understate the member's
real remaining allowance.

None of this changes the allowance/master debit AMOUNTS themselves — the
company is charged exactly the same total (fare + tip) as before #4074.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

RIDE_ID = "ride_tip_report_1"
RIDER_ID = "rider_tip_report_1"
COMPANY_ID = "company_1"
MEMBER_ID = "member_1"
ALLOWANCE_ID = "allowance_1"
CORP_WALLET_ID = "corp_wallet_1"

_RIDE = {
    "id": RIDE_ID,
    "rider_id": RIDER_ID,
    "corporate_account_id": COMPANY_ID,
    "corporate_member_id": MEMBER_ID,
}


def _membership(**extra) -> dict:
    row = {"id": MEMBER_ID, "company_id": COMPANY_ID, "status": "active", "user_id": RIDER_ID}
    row.update(extra)
    return row


def _allowance(**extra) -> dict:
    row = {"id": ALLOWANCE_ID, "type": "fixed_recurring", "amount": "1000.00", "used": "0.00"}
    row.update(extra)
    return row


@contextlib.contextmanager
def _settle_corporate_patches(*, insert_one=None, evaluate_policy=None):
    """The standard settle_corporate mock set, matching
    tests/test_corporate_rpc_ride_idempotency.py's TestSettleCorporateRideIdThreading.
    """
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
                AsyncMock(return_value=_membership()),
            )
        )
        stack.enter_context(
            patch(
                "backend.services.payment_service.db_supabase.get_member_allowance",
                AsyncMock(return_value=_allowance()),
            )
        )
        stack.enter_context(
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
                AsyncMock(return_value={"id": CORP_WALLET_ID}),
            )
        )
        stack.enter_context(
            patch("backend.services.payment_service.db_supabase.get_corporate_policy", AsyncMock(return_value={}))
        )
        stack.enter_context(patch("backend.services.payment_service.db_supabase.insert_one", insert_one or AsyncMock()))
        stack.enter_context(patch("backend.services.payment_service.db_supabase.update_ride", AsyncMock()))
        stack.enter_context(
            patch(
                "backend.services.payment_service.corporate_allowance_service.apply_ride_debit",
                AsyncMock(return_value={"deduped": False}),
            )
        )
        stack.enter_context(
            patch(
                "backend.services.payment_service.evaluate_policy",
                evaluate_policy or (lambda *a, **k: {"pass": True}),
            )
        )
        yield


class TestSettleCorporateTipReporting:
    async def test_ride_payment_sources_records_tip_amount_separately(self):
        from backend.services.payment_service import settle_corporate

        insert_one = AsyncMock()
        with _settle_corporate_patches(insert_one=insert_one):
            # $45.00 fare + $20.00 tip = $65.00 total_charge, as
            # routes/rides/payments.py builds it.
            result = await settle_corporate(_RIDE, RIDE_ID, Decimal("65.00"), Decimal("20.00"))

        assert result.success is True
        insert_call = next(c for c in insert_one.await_args_list if c.args[0] == "ride_payment_sources")
        payload = insert_call.args[1]
        assert payload["tip_amount"] == 20.00
        # The company is still charged the FULL tip-inclusive total — #4074
        # only fixed reporting, not who pays.
        assert Decimal(payload["allowance_debit_amount"]) + Decimal(payload["master_fallback_amount"]) == Decimal(
            "65.00"
        )

    async def test_zero_tip_records_zero_tip_amount(self):
        from backend.services.payment_service import settle_corporate

        insert_one = AsyncMock()
        with _settle_corporate_patches(insert_one=insert_one):
            result = await settle_corporate(_RIDE, RIDE_ID, Decimal("45.00"), Decimal("0.00"))

        assert result.success is True
        insert_call = next(c for c in insert_one.await_args_list if c.args[0] == "ride_payment_sources")
        assert insert_call.args[1]["tip_amount"] == 0.00

    async def test_policy_final_fare_excludes_tip(self):
        """A generous tip must not push a ride over a max_fare_per_ride cap
        that the trip's actual fare alone would have passed — the same
        false-positive #4074 reported. Booking-phase evaluation compares
        estimated_fare (never tip-inclusive); completion-phase must match."""
        from backend.services.payment_service import settle_corporate

        captured_ctx = {}

        def _capture_evaluate_policy(policy, ride_context):
            captured_ctx.update(ride_context)
            return {"pass": True}

        with _settle_corporate_patches(evaluate_policy=_capture_evaluate_policy):
            result = await settle_corporate(_RIDE, RIDE_ID, Decimal("65.00"), Decimal("20.00"))

        assert result.success is True
        # $65.00 total - $20.00 tip = $45.00 fare-only, NOT $65.00.
        assert captured_ctx["final_fare"] == 45.00

    async def test_tip_exceeding_total_clamps_fare_only_to_zero_defensively(self):
        """Not a real caller shape (payments.py always builds total_charge as
        fare + tip), but settle_corporate must never feed a negative
        final_fare into policy evaluation on a bad input."""
        from backend.services.payment_service import settle_corporate

        captured_ctx = {}

        def _capture_evaluate_policy(policy, ride_context):
            captured_ctx.update(ride_context)
            return {"pass": True}

        with _settle_corporate_patches(evaluate_policy=_capture_evaluate_policy):
            result = await settle_corporate(_RIDE, RIDE_ID, Decimal("10.00"), Decimal("20.00"))

        assert result.success is True
        assert captured_ctx["final_fare"] == 0.00


class TestChargeLateCorporateTipReporting:
    async def test_late_tip_increments_tip_amount_on_existing_row(self):
        from backend.services import payment_service

        existing_row = {
            "ride_id": RIDE_ID,
            "allowance_debit_amount": 20.00,
            "master_fallback_amount": 0.00,
            "tip_amount": 0.00,
        }

        with (
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
                AsyncMock(return_value=_membership()),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_member_allowance",
                AsyncMock(return_value=_allowance(amount="100.00", used="10.00")),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
                AsyncMock(return_value={"id": CORP_WALLET_ID}),
            ),
            patch(
                "backend.services.corporate_allowance_service.apply_late_tip_debit",
                AsyncMock(return_value={"deduped": False}),
            ),
            patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=existing_row)),
            patch("backend.services.payment_service.db_supabase.update_one", AsyncMock(return_value={})) as mock_upd,
        ):
            collected = await payment_service.charge_late_corporate_tip(
                {**_RIDE, "payment_method": "company_allowance", "payment_status": "paid"}, RIDE_ID, Decimal("5.00")
            )

        assert collected == Decimal("5.00")
        args = mock_upd.await_args
        # $0 existing tip_amount + $5 collected late tip.
        assert args.args[2]["tip_amount"] == 5.00

    async def test_late_tip_accumulates_onto_existing_tip_amount(self):
        """A ride that already had an in-flow tip (settle_corporate) recorded,
        then gets a SECOND late tip — the two must sum, not overwrite."""
        from backend.services import payment_service

        existing_row = {
            "ride_id": RIDE_ID,
            "allowance_debit_amount": 45.00,
            "master_fallback_amount": 0.00,
            "tip_amount": 8.00,  # original in-flow tip
        }

        with (
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
                AsyncMock(return_value=_membership()),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_member_allowance",
                AsyncMock(return_value=_allowance(amount="100.00", used="10.00")),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
                AsyncMock(return_value={"id": CORP_WALLET_ID}),
            ),
            patch(
                "backend.services.corporate_allowance_service.apply_late_tip_debit",
                AsyncMock(return_value={"deduped": False}),
            ),
            patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=existing_row)),
            patch("backend.services.payment_service.db_supabase.update_one", AsyncMock(return_value={})) as mock_upd,
        ):
            collected = await payment_service.charge_late_corporate_tip(
                {**_RIDE, "payment_method": "company_allowance", "payment_status": "paid"}, RIDE_ID, Decimal("3.00")
            )

        assert collected == Decimal("3.00")
        args = mock_upd.await_args
        assert args.args[2]["tip_amount"] == 11.00  # 8 original + 3 late


class TestAggregateRowsTipTotal:
    def test_tip_total_summed_and_present_in_summary(self):
        from backend.routes.corporate_company import _aggregate_rows

        rows = [
            {"allowance_debit_amount": "45.00", "master_fallback_amount": "0.00", "tip_amount": "10.00"},
            {"allowance_debit_amount": "30.00", "master_fallback_amount": "5.00", "tip_amount": "0.00"},
        ]

        summary = _aggregate_rows(rows)

        assert summary["tip_total"] == "10.00"
        # tip_total is a breakdown, not additive -- the grand total is
        # unchanged from what allowance_total + master_total already gave.
        assert summary["total"] == "80.00"

    def test_missing_tip_amount_column_defaults_to_zero(self):
        """Rows written before migration 407 (or a stale cached read) have no
        tip_amount key at all -- must not raise, must treat as $0."""
        from backend.routes.corporate_company import _aggregate_rows

        rows = [{"allowance_debit_amount": "45.00", "master_fallback_amount": "0.00"}]

        summary = _aggregate_rows(rows)

        assert summary["tip_total"] == "0.00"
