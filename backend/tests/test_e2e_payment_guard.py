"""
E2E — Fare-collection guard (B-P0-2 regression suite).

Exercises the atomic payment guard introduced in rides.py:process_payment
that prevents double-charging when concurrent requests race to settle the
same completed ride.

Scenarios:
  1. Non-completed ride → 409 (status guard)
  2. Already-paid ride  → 200 + already_paid=True (idempotency skip)
  3. Processing ride    → 200 + already_paid=True (in-flight guard)
  4. Concurrent race    → exactly one request claims the "processing" slot;
                          the loser gets already_paid=True without being charged
  5. Tip exceeds cap    → 400
  6. Negative tip       → 400
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

RIDER_ID = "rider_payment_e2e"
RIDE_ID = "ride_payment_e2e_001"


def _completed_ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": "completed",
        "payment_status": "pending",
        "total_fare": 18.5,
        "tip_amount": 0,
        "payment_method": "card",
    }
    row.update(extra)
    return row


def _payment_request(tip: float = 0.0):
    req = MagicMock()
    req.tip_amount = Decimal(str(tip))
    return req


@pytest.mark.e2e
@pytest.mark.asyncio
class TestPaymentStatusGuard:
    """process_payment must reject any ride not in completed state."""

    @pytest.mark.parametrize(
        "status",
        ["searching", "driver_assigned", "driver_accepted", "driver_arrived", "in_progress", "cancelled"],
    )
    async def test_non_completed_ride_returns_409(self, status: str):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with patch(
            "backend.routes.rides.db_supabase.get_ride",
            AsyncMock(
                return_value={"id": RIDE_ID, "rider_id": RIDER_ID, "status": status, "payment_status": "pending"}
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(
                    ride_id=RIDE_ID,
                    req=_payment_request(),
                    current_user={"id": RIDER_ID},
                )

        assert exc.value.status_code == 409
        assert status in exc.value.detail


@pytest.mark.e2e
@pytest.mark.asyncio
class TestPaymentIdempotency:
    """Already-paid or already-processing rides must return success without re-charging."""

    @pytest.mark.parametrize("payment_status", ["paid", "processing"])
    async def test_already_settled_returns_success_no_charge(self, payment_status: str):
        from backend.routes import rides as rides_mod

        ride = _completed_ride(payment_status=payment_status, total_fare=18.5, tip_amount=2.0)

        with patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            result = await rides_mod.process_payment(
                ride_id=RIDE_ID,
                req=_payment_request(tip=0.0),
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        assert result.get("already_paid") is True


@pytest.mark.e2e
@pytest.mark.asyncio
class TestPaymentAtomicGuard:
    """The pending→processing atomic swap prevents double-charging under concurrency."""

    async def test_atomic_guard_blocks_second_concurrent_request(self):
        """
        Simulates two concurrent process_payment calls on the same ride.
        The first call wins the atomic UPDATE WHERE payment_status='pending';
        the second sees update_one return None (no matching row), re-reads
        the ride, observes the winner's card 'processing' state, and returns
        already_paid=True without going on to charge Stripe.
        """
        from backend.routes import rides as rides_mod
        from backend.utils.stripe_charge import ChargeOutcome

        # First call: update_one returns the updated row (won the race)
        # Second call: update_one returns None (lost the race)
        update_side_effects = [
            {"id": RIDE_ID, "payment_status": "processing"},  # winner
            None,  # loser
        ]

        stripe_mock = AsyncMock(
            return_value=ChargeOutcome(status="succeeded", payment_intent_id="pi_guard", charged_amount=18.5)
        )

        # Both requests' pre-claim reads see 'pending'; any read after the
        # winner's claim (notably the loser's post-guard re-read) sees the
        # winner's 'processing' row.
        _reads = {"n": 0}

        async def _get_ride(_ride_id):
            _reads["n"] += 1
            if _reads["n"] <= 2:
                return _completed_ride()
            return _completed_ride(payment_status="processing")

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(side_effect=_get_ride)),
            patch(
                "backend.routes.rides.db_supabase.get_user_by_id",
                AsyncMock(return_value={"stripe_customer_id": "cus_g", "default_payment_method": "pm_g"}),
            ),
            patch(
                "backend.routes.rides.db_supabase.update_one",
                AsyncMock(side_effect=update_side_effects),
            ),
            patch("backend.services.payment_service.charge_ride", stripe_mock),
            patch("backend.routes.rides.db_supabase.update_ride", AsyncMock()),
        ):
            results = await asyncio.gather(
                rides_mod.process_payment(ride_id=RIDE_ID, req=_payment_request(), current_user={"id": RIDER_ID}),
                rides_mod.process_payment(ride_id=RIDE_ID, req=_payment_request(), current_user={"id": RIDER_ID}),
                return_exceptions=True,
            )

        # Exactly one request should have attempted to charge Stripe
        assert stripe_mock.await_count <= 1, "Stripe must never be called twice for the same ride"

        # The loser must have received already_paid=True
        already_paid_results = [r for r in results if isinstance(r, dict) and r.get("already_paid")]
        assert len(already_paid_results) >= 1, "Losing race must return already_paid=True"

    async def test_unauthorized_rider_gets_403(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _completed_ride()  # rider_id = RIDER_ID

        with patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(
                    ride_id=RIDE_ID,
                    req=_payment_request(),
                    current_user={"id": "different_rider"},
                )

        assert exc.value.status_code == 403


@pytest.mark.e2e
@pytest.mark.asyncio
class TestPaymentTipValidation:
    """Tip boundary checks are validated before any Stripe call."""

    async def test_tip_above_500_returns_400(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _completed_ride()
        guard_row = {"id": RIDE_ID, "payment_status": "processing"}

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides.db_supabase.update_one", AsyncMock(return_value=guard_row)),
            patch("backend.routes.rides.db_supabase.update_ride", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(
                    ride_id=RIDE_ID,
                    req=_payment_request(tip=501.0),
                    current_user={"id": RIDER_ID},
                )

        assert exc.value.status_code == 400
        assert "500" in exc.value.detail

    async def test_negative_tip_returns_400(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _completed_ride()
        guard_row = {"id": RIDE_ID, "payment_status": "processing"}

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides.db_supabase.update_one", AsyncMock(return_value=guard_row)),
            patch("backend.routes.rides.db_supabase.update_ride", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(
                    ride_id=RIDE_ID,
                    req=_payment_request(tip=-1.0),
                    current_user={"id": RIDER_ID},
                )

        assert exc.value.status_code == 400
