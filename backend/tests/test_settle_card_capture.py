"""
Unit tests for the capture-against-hold settlement path in
backend/services/payment_service.py::settle_card / _settle_against_hold.

These pin the subtask-4 behavior: when a ride carries a booking-time
pre-authorization hold (auth_status in authorized/fare_only), settlement
CAPTURES that PaymentIntent for (fare + tip) in one Stripe fee instead of
creating a fresh charge. Covered branches:

  - within-buffer tip → single capture, paid, auth_status=captured
  - tip over buffer    → capture the hold + fresh charge for the overflow
  - over-buffer overflow charge fails → settle captured portion, log, paid
  - capture declined (issuer reversal) → failed + 402
  - capture failed (expired hold)      → fall back to a fresh full charge
  - no hold present                    → unchanged fresh-charge path

capture_ride / charge_ride are patched on the payment_service bound names.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

RIDE_ID = "ride_cap_1"
RIDER_ID = "rider_cap_1"


def _outcome(**kw):
    from backend.utils.stripe_charge import ChargeOutcome

    return ChargeOutcome(**kw)


def _held_ride(auth_status="authorized", authorized_amount="35.00", pi="pi_hold"):
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "payment_method": "card",
        "payment_intent_id": pi,
        "auth_status": auth_status,
        "authorized_amount": authorized_amount,
        "total_fare": "25.00",
        "driver_earnings": "20.00",
        "tip_amount": "0",
    }


def _common_patches(*, capture=None, charge=None):
    """Patch DB writes, ledger, socket, push, and the Stripe helpers.
    Returns (patches, updates) where updates captures every update_ride patch."""
    updates = []

    async def _capture_update(ride_id, patch):
        updates.append(patch)
        return {"modified_count": 1}

    ps = "backend.services.payment_service."
    patches = [
        patch(
            ps + "db_supabase.get_user_by_id",
            AsyncMock(return_value={"stripe_customer_id": "cus_X", "default_payment_method": "pm_X"}),
        ),
        patch(ps + "db_supabase.update_ride", AsyncMock(side_effect=_capture_update)),
        patch(ps + "db_supabase.insert_one", AsyncMock(return_value=None)),
        patch(ps + "record_payment_event", AsyncMock(return_value=None)),
        patch(ps + "manager.send_personal_message", AsyncMock(return_value=None)),
        patch(ps + "send_push_notification", AsyncMock(return_value=None)),
    ]
    if capture is not None:
        patches.append(
            patch(ps + "capture_ride", AsyncMock(side_effect=capture if isinstance(capture, list) else [capture]))
        )
    if charge is not None:
        patches.append(
            patch(ps + "charge_ride", AsyncMock(side_effect=charge if isinstance(charge, list) else [charge]))
        )
    return patches, updates


def _last(updates, key):
    for u in reversed(updates):
        if key in u:
            return u[key]
    return None


@pytest.mark.asyncio
class TestCaptureWithinBuffer:
    async def test_within_buffer_tip_single_capture(self):
        """Fare 25 + tip 5 = 30 ≤ 35 hold → one capture, no fresh charge."""
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        cap = _outcome(status="captured", payment_intent_id="pi_hold", charged_amount=Decimal("30.00"))
        patches, updates = _common_patches(capture=cap, charge=_outcome(status="failed"))

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            result = await settle_card(_held_ride(), RIDE_ID, RIDER_ID, Decimal("30.00"), Decimal("5.00"))

        assert result.success is True
        assert result.charged_amount == "30.00"
        assert _last(updates, "payment_status") == "paid"
        assert _last(updates, "auth_status") == "captured"
        assert _last(updates, "payment_intent_id") == "pi_hold"


@pytest.mark.asyncio
class TestCaptureOverBuffer:
    async def test_tip_over_buffer_captures_hold_plus_fresh_charge(self):
        """Fare 25 + tip 20 = 45 > 35 hold → capture 35 + charge 10 overflow."""
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        cap = _outcome(status="captured", payment_intent_id="pi_hold", charged_amount=Decimal("35.00"))
        over = _outcome(status="succeeded", payment_intent_id="pi_over", charged_amount=Decimal("10.00"))
        patches, updates = _common_patches(capture=cap, charge=over)

        with ExitStack() as st:
            ctxs = [st.enter_context(p) for p in patches]
            # capture_ride is the 2nd-to-last patch, charge_ride the last
            charge_mock = ctxs[-1]
            result = await settle_card(_held_ride(), RIDE_ID, RIDER_ID, Decimal("45.00"), Decimal("20.00"))

        assert result.success is True
        assert result.charged_amount == "45.00"  # 35 captured + 10 overflow
        # overflow charge was for the remainder only
        assert charge_mock.call_args.kwargs["total_amount"] == Decimal("10.00")
        assert _last(updates, "payment_status") == "paid"

    async def test_over_buffer_overflow_charge_fails_settles_captured_portion(self):
        """Overflow charge declines → fare+buffer captured, excess tip dropped,
        ride still marked paid (not stranded)."""
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        cap = _outcome(status="captured", payment_intent_id="pi_hold", charged_amount=Decimal("35.00"))
        over = _outcome(status="declined", decline_code="insufficient_funds")
        patches, updates = _common_patches(capture=cap, charge=over)

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            result = await settle_card(_held_ride(), RIDE_ID, RIDER_ID, Decimal("45.00"), Decimal("20.00"))

        assert result.success is True
        assert result.charged_amount == "35.00"  # only the captured hold
        assert _last(updates, "payment_status") == "paid"


@pytest.mark.asyncio
class TestCaptureFailureModes:
    async def test_capture_declined_marks_failed_402(self):
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        cap = _outcome(status="declined", decline_code="issuer_not_available")
        patches, updates = _common_patches(capture=cap)

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            result = await settle_card(_held_ride(), RIDE_ID, RIDER_ID, Decimal("30.00"), Decimal("5.00"))

        assert result.success is False
        assert result.status_code == 402
        assert result.error_code == "card_declined"
        assert _last(updates, "payment_status") == "failed"

    async def test_expired_hold_falls_back_to_fresh_charge(self):
        """capture 'failed' (expired) → settle_card creates a fresh full charge,
        and must NOT reconfirm the dead hold PI (payment_intent_id=None)."""
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        cap = _outcome(status="failed", error_message="PaymentIntent expired")
        fresh = _outcome(status="succeeded", payment_intent_id="pi_fresh", charged_amount=Decimal("30.00"))
        patches, updates = _common_patches(capture=cap, charge=fresh)

        with ExitStack() as st:
            ctxs = [st.enter_context(p) for p in patches]
            charge_mock = ctxs[-1]
            result = await settle_card(_held_ride(), RIDE_ID, RIDER_ID, Decimal("30.00"), Decimal("5.00"))

        assert result.success is True
        assert result.charged_amount == "30.00"
        # fresh charge, not a reconfirm of the dead hold
        assert charge_mock.call_args.kwargs["payment_intent_id"] is None


@pytest.mark.asyncio
class TestNoHoldUnchanged:
    async def test_no_hold_uses_fresh_charge_path(self):
        """A ride with no auth_status hold settles via charge_ride exactly as
        before; capture_ride is never called."""
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        ride = _held_ride(auth_status=None, authorized_amount=0, pi=None)
        fresh = _outcome(status="succeeded", payment_intent_id="pi_fresh", charged_amount=Decimal("30.00"))
        cap_mock = AsyncMock()
        patches, updates = _common_patches(charge=fresh)
        patches.append(patch("backend.services.payment_service.capture_ride", cap_mock))

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            result = await settle_card(ride, RIDE_ID, RIDER_ID, Decimal("30.00"), Decimal("5.00"))

        assert result.success is True
        cap_mock.assert_not_called()
