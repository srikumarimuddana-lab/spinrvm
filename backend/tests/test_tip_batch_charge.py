"""
Tip batch-charge loop: collection triggers, replay safety, and the money rule.

The invariant that matters most here: a driver is credited ONLY when a charge
actually succeeds. Crediting on record is the exact bug this subsystem exists to
fix, so a failed collection must leave driver_earnings untouched.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

TBC = "backend.utils.tip_batch_charge."


def _tip(amount="2.00", days_old=0, status="owed", tip_id="pt_1", ride_id="ride_1"):
    return {
        "id": tip_id,
        "ride_id": ride_id,
        "rider_id": "rider_1",
        "driver_id": "driver_1",
        "amount": amount,
        "status": status,
        "attempts": 0,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat(),
    }


def _outcome(**kw):
    from backend.utils.stripe_charge import ChargeOutcome

    return ChargeOutcome(**kw)


@pytest.mark.unit
class TestIsDue:
    def test_small_recent_debt_waits(self):
        """A lone $2 tip loses 18% to Stripe's fixed fee — worth waiting."""
        from backend.utils.tip_batch_charge import _is_due

        assert _is_due([_tip("2.00", days_old=1)], datetime.now(timezone.utc)) is False

    def test_threshold_reached_collects(self):
        from backend.utils.tip_batch_charge import _is_due

        rows = [_tip("4.00", tip_id="a"), _tip("6.00", tip_id="b")]
        assert _is_due(rows, datetime.now(timezone.utc)) is True

    def test_old_debt_collects_even_when_small(self):
        """The ceiling that stops a driver waiting forever on a lapsed rider."""
        from backend.utils.tip_batch_charge import _is_due

        assert _is_due([_tip("2.00", days_old=8)], datetime.now(timezone.utc)) is True

    def test_unparseable_timestamp_collects_rather_than_stranding(self):
        from backend.utils.tip_batch_charge import _is_due

        row = _tip("2.00")
        row["created_at"] = "not-a-date"
        assert _is_due([row], datetime.now(timezone.utc)) is True


@pytest.mark.unit
@pytest.mark.asyncio
class TestChargeRiderBatch:
    def _patches(self, st, *, claim, charge, update=None, credit=None):
        st.enter_context(patch(TBC + "db_supabase.update_one", claim))
        st.enter_context(
            patch(TBC + "db_supabase.get_user_by_id", AsyncMock(return_value={"stripe_customer_id": "cus_1"}))
        )
        st.enter_context(
            patch(
                TBC + "db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_1", "payment_method_id": "pm_1", "driver_earnings": "20.00"}),
            )
        )
        st.enter_context(patch(TBC + "db_supabase.update_ride", update or AsyncMock()))
        st.enter_context(patch(TBC + "charge_ancillary_fee", charge))
        if credit is not None:
            st.enter_context(patch(TBC + "_credit_driver_for_tip", credit))

    async def test_successful_batch_charges_the_sum_once(self):
        from backend.utils.tip_batch_charge import _charge_rider_batch

        claim = AsyncMock(return_value={"id": "pt_1"})
        charge = AsyncMock(return_value=_outcome(status="succeeded", payment_intent_id="pi_batch"))
        credit = AsyncMock()

        with ExitStack() as st:
            self._patches(st, claim=claim, charge=charge, credit=credit)
            await _charge_rider_batch("rider_1", [_tip("4.00", tip_id="a"), _tip("6.00", tip_id="b")])

        charge.assert_awaited_once()
        # One charge for the sum, not one per tip — the whole point of batching.
        assert charge.call_args.kwargs["amount"] == Decimal("10.00")
        assert charge.call_args.kwargs["fee_type"] == "tip_batch"
        assert credit.await_count == 2

    async def test_failed_charge_credits_nobody(self):
        """The money rule. A driver must never be credited for an uncollected tip."""
        from backend.utils.tip_batch_charge import _charge_rider_batch

        claim = AsyncMock(return_value={"id": "pt_1"})
        charge = AsyncMock(return_value=_outcome(status="declined", error_message="card_declined"))
        update_ride = AsyncMock()

        with ExitStack() as st:
            self._patches(st, claim=claim, charge=charge, update=update_ride)
            await _charge_rider_batch("rider_1", [_tip("10.00")])

        update_ride.assert_not_awaited()
        # Row released back for retry, with the attempt counted.
        released = [c for c in claim.call_args_list if c.args[2]["$set"].get("status") == "failed"]
        assert released
        assert released[-1].args[2]["$set"]["attempts"] == 1

    async def test_lost_claim_race_charges_nothing(self):
        """Second replica gets None from the claim and must not charge."""
        from backend.utils.tip_batch_charge import _charge_rider_batch

        claim = AsyncMock(return_value=None)  # every claim lost
        charge = AsyncMock()

        with ExitStack() as st:
            self._patches(st, claim=claim, charge=charge)
            await _charge_rider_batch("rider_1", [_tip("10.00")])

        charge.assert_not_awaited()

    async def test_claim_asserts_the_status_that_was_read(self):
        """The claim filter must pin status, or two replicas both 'win'."""
        from backend.utils.tip_batch_charge import _charge_rider_batch

        claim = AsyncMock(return_value={"id": "pt_1"})
        charge = AsyncMock(return_value=_outcome(status="succeeded", payment_intent_id="pi_1"))

        with ExitStack() as st:
            self._patches(st, claim=claim, charge=charge, credit=AsyncMock())
            await _charge_rider_batch("rider_1", [_tip("10.00", status="failed")])

        first_filter = claim.call_args_list[0].args[1]
        assert first_filter["status"] == "failed"
        assert claim.call_args_list[0].args[2]["$set"]["status"] == "charging"

    async def test_missing_card_does_not_retry_forever(self):
        from backend.utils.tip_batch_charge import _charge_rider_batch

        claim = AsyncMock(return_value={"id": "pt_1"})
        charge = AsyncMock()

        with ExitStack() as st:
            st.enter_context(patch(TBC + "db_supabase.update_one", claim))
            st.enter_context(patch(TBC + "db_supabase.get_user_by_id", AsyncMock(return_value={})))
            st.enter_context(patch(TBC + "db_supabase.get_ride", AsyncMock(return_value={"id": "ride_1"})))
            st.enter_context(patch(TBC + "charge_ancillary_fee", charge))
            await _charge_rider_batch("rider_1", [_tip("10.00")])

        charge.assert_not_awaited()
        assert claim.call_args_list[-1].args[2]["$set"]["status"] == "failed"


@pytest.mark.unit
@pytest.mark.asyncio
class TestCreditDriver:
    async def test_credit_adds_tip_to_earnings_and_snapshot(self):
        from backend.utils.tip_batch_charge import _credit_driver_for_tip

        update = AsyncMock()
        ride = {
            "id": "ride_1",
            "tip_amount": "0",
            "driver_earnings": "20.00",
            "driver_earnings_snapshot": {"fare": "20.00", "tip": "0", "incentive": 0, "tax": 0, "cancel_fee": 0},
        }
        with ExitStack() as st:
            st.enter_context(patch(TBC + "db_supabase.get_ride", AsyncMock(return_value=ride)))
            st.enter_context(patch(TBC + "db_supabase.update_ride", update))
            await _credit_driver_for_tip(_tip("2.00"), "pi_1")

        written = update.call_args.args[1]
        assert written["tip_amount"] == 2.00
        assert written["driver_earnings"] == 22.00
        # Snapshot feeds T4A, so it must move with the earnings.
        assert "driver_earnings_snapshot" in written


@pytest.mark.unit
@pytest.mark.asyncio
class TestDisputeReadiness:
    """One PaymentIntent can cover several rides. A rider asking "what is this
    $7 charge?" must be answerable from the charge itself — the anchor ride_id
    alone cannot do that."""

    async def test_charge_metadata_names_every_ride_and_its_split(self):
        from backend.utils.tip_batch_charge import _charge_rider_batch

        charge = AsyncMock(return_value=_outcome(status="succeeded", payment_intent_id="pi_1"))
        with ExitStack() as st:
            st.enter_context(patch(TBC + "db_supabase.update_one", AsyncMock(return_value={"id": "x"})))
            st.enter_context(
                patch(
                    TBC + "db_supabase.get_user_by_id",
                    AsyncMock(return_value={"stripe_customer_id": "cus_1"}),
                )
            )
            st.enter_context(
                patch(
                    TBC + "db_supabase.get_ride",
                    AsyncMock(return_value={"id": "ride_a", "payment_method_id": "pm_1"}),
                )
            )
            st.enter_context(patch(TBC + "charge_ancillary_fee", charge))
            st.enter_context(patch(TBC + "_credit_driver_for_tip", AsyncMock()))
            await _charge_rider_batch(
                "rider_1",
                [
                    _tip("4.00", tip_id="a", ride_id="ride_a"),
                    _tip("6.00", tip_id="b", ride_id="ride_b"),
                ],
            )

        meta = charge.call_args.kwargs["extra_metadata"]
        assert "ride_a" in meta["tip_ride_ids"] and "ride_b" in meta["tip_ride_ids"]
        # Per-ride split, so support can explain the total without a DB query.
        assert "ride_a:4.00" in meta["tip_breakdown"]
        assert "ride_b:6.00" in meta["tip_breakdown"]
        assert meta["tip_count"] == "2"

    async def test_metadata_values_stay_within_stripes_500_char_cap(self):
        from backend.utils.tip_batch_charge import _charge_rider_batch

        charge = AsyncMock(return_value=_outcome(status="succeeded", payment_intent_id="pi_1"))
        many = [_tip("1.00", tip_id=f"t{i}", ride_id=f"ride_{i:04d}_long_identifier") for i in range(60)]
        with ExitStack() as st:
            st.enter_context(patch(TBC + "db_supabase.update_one", AsyncMock(return_value={"id": "x"})))
            st.enter_context(
                patch(
                    TBC + "db_supabase.get_user_by_id",
                    AsyncMock(return_value={"stripe_customer_id": "cus_1"}),
                )
            )
            st.enter_context(
                patch(TBC + "db_supabase.get_ride", AsyncMock(return_value={"id": "r", "payment_method_id": "pm_1"}))
            )
            st.enter_context(patch(TBC + "charge_ancillary_fee", charge))
            st.enter_context(patch(TBC + "_credit_driver_for_tip", AsyncMock()))
            await _charge_rider_batch("rider_1", many)

        meta = charge.call_args.kwargs["extra_metadata"]
        # Truncated, not rejected — a long batch must not fail the charge.
        assert len(meta["tip_ride_ids"]) <= 500
        assert len(meta["tip_breakdown"]) <= 500
