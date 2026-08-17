"""
Unit tests for the capture-against-hold settlement path in
backend/services/payment_service.py::settle_card / _settle_against_hold.

These pin the subtask-4 behavior: when a ride carries a booking-time
pre-authorization hold (auth_status in authorized/fare_only), settlement
CAPTURES that PaymentIntent for (fare + tip) in one Stripe fee instead of
creating a fresh charge. Covered branches:

  - tip within the hold → single capture, paid, auth_status=captured
  - tip over the hold   → capture the hold + fresh charge for the overflow
  - overflow charge fails → settle captured portion, log, paid
  - incrementable hold  → raise the hold, then ONE capture (no second fee)
  - increment absent/declined → capture + separate tip charge (two fees)
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


def _fresh_ride(**overrides):
    """A ride with no open pre-auth hold, so settle_card takes the direct
    charge_ride path instead of _settle_against_hold."""
    base = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "payment_method": "card",
        "payment_intent_id": None,
        "auth_status": None,
        "authorized_amount": "0",
        "total_fare": "25.00",
        "driver_earnings": "20.00",
        "tip_amount": "0",
    }
    base.update(overrides)
    return base


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
class TestFreshChargeFailurePushTargetApp:
    """N10 regression: settle_card's rider-facing 'Payment failed' push (no
    pre-auth hold, straight charge_ride path) must pass target_app='rider'.
    Fails if target_app reverts to the omitted default."""

    async def test_declined_fresh_charge_pushes_rider_with_target_app(self):
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        declined = _outcome(status="declined", decline_code="card_declined", error_message="Declined")
        patches, updates = _common_patches(charge=declined)

        with ExitStack() as st:
            ctxs = [st.enter_context(p) for p in patches]
            push_mock = ctxs[5]  # position fixed by _common_patches: ...,send_personal_message,send_push_notification
            result = await settle_card(_fresh_ride(), RIDE_ID, RIDER_ID, Decimal("25.00"), Decimal("0"))

        assert result.success is False
        assert result.error_code == "card_declined"
        push_mock.assert_awaited_once()
        assert push_mock.await_args.args[0] == RIDER_ID
        assert push_mock.await_args.kwargs.get("target_app") == "rider"

    async def test_generic_failure_fresh_charge_pushes_rider_with_target_app(self):
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        failed = _outcome(status="failed", error_message="Something went wrong")
        patches, updates = _common_patches(charge=failed)

        with ExitStack() as st:
            ctxs = [st.enter_context(p) for p in patches]
            push_mock = ctxs[5]
            result = await settle_card(_fresh_ride(), RIDE_ID, RIDER_ID, Decimal("25.00"), Decimal("0"))

        assert result.success is False
        assert result.error_code == "payment_error"
        push_mock.assert_awaited_once()
        assert push_mock.await_args.args[0] == RIDER_ID
        assert push_mock.await_args.kwargs.get("target_app") == "rider"


@pytest.mark.asyncio
class TestChangeCardOverride:
    """The in-app 'Change Card' escape: payment_method_id_override forces a
    fresh charge on the chosen card and never captures the booking-time hold
    (which sits on the rejected card)."""

    async def test_override_fresh_charges_new_card_ignoring_hold(self):
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        # Ride HAS an open hold on the old card — override must skip it.
        fresh = _outcome(status="succeeded", payment_intent_id="pi_new", charged_amount=Decimal("30.00"))
        cap_mock = AsyncMock()
        # Codex round-3 (#6): the old card's uncaptured hold must be cancelled so
        # the rider's funds aren't reserved until auth expiry.
        cancel_mock = AsyncMock(return_value=True)
        patches, updates = _common_patches(charge=fresh)
        patches.append(patch("backend.services.payment_service.capture_ride", cap_mock))
        patches.append(patch("backend.services.payment_service.cancel_authorization", cancel_mock))

        with ExitStack() as st:
            ctxs = [st.enter_context(p) for p in patches]
            charge_mock = ctxs[-3]  # charge_ride, then capture_ride, then cancel_authorization
            result = await settle_card(
                _held_ride(),
                RIDE_ID,
                RIDER_ID,
                Decimal("30.00"),
                Decimal("5.00"),
                payment_method_id_override="pm_NEW",
            )

        assert result.success is True
        # Hold never captured; fresh PI (no reconfirm of the dead hold).
        cap_mock.assert_not_called()
        assert charge_mock.call_args.kwargs["payment_method_id"] == "pm_NEW"
        assert charge_mock.call_args.kwargs["payment_intent_id"] is None
        # The old hold PI was cancelled and the ride marked auth released.
        cancel_mock.assert_awaited_once()
        assert cancel_mock.await_args.kwargs["payment_intent_id"] == "pi_hold"
        assert _last(updates, "auth_status") == "released"
        # Ride re-pointed to the card actually charged.
        assert _last(updates, "payment_method_id") == "pm_NEW"
        assert _last(updates, "payment_status") == "paid"
        # Codex P2 (62jG): the cached brand/last4 of the OLD declined card are
        # cleared so the admin ride-detail resolver re-derives them from the new
        # PaymentIntent instead of showing the rejected card.
        paid_update = next(u for u in reversed(updates) if u.get("payment_status") == "paid")
        assert paid_update["card_brand"] is None
        assert paid_update["card_last4"] is None

    async def test_override_decline_does_not_release_old_hold(self):
        """Codex round-3 (#6 follow-up, P1): the old hold must be released only
        AFTER the new card charges. If the new card declines, the guaranteed
        authorization must be left intact (not cancelled) so fare is still
        collectable."""
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        # New card declines.
        declined = _outcome(status="declined", decline_code="card_declined", error_message="Declined")
        cancel_mock = AsyncMock(return_value=True)
        patches, updates = _common_patches(charge=declined)
        patches.append(patch("backend.services.payment_service.capture_ride", AsyncMock()))
        patches.append(patch("backend.services.payment_service.cancel_authorization", cancel_mock))

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            result = await settle_card(
                _held_ride(),
                RIDE_ID,
                RIDER_ID,
                Decimal("30.00"),
                Decimal("5.00"),
                payment_method_id_override="pm_NEW",
            )

        assert result.success is False
        # The old hold was NOT cancelled — it stays authorized and collectable.
        cancel_mock.assert_not_awaited()


@pytest.mark.asyncio
class TestNoPaymentMethod:
    async def test_no_card_returns_structured_402_change_card(self):
        """No override, no ride card, no default → 402 no_payment_method with a
        change_card hint so the app shows Add Card instead of a dead-end."""
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        ride = _held_ride(auth_status=None, authorized_amount=0, pi=None)
        ride["payment_method_id"] = None
        patches, updates = _common_patches()
        # User has no default payment method either.
        patches[0] = patch(
            "backend.services.payment_service.db_supabase.get_user_by_id",
            AsyncMock(return_value={"stripe_customer_id": "cus_X", "default_payment_method": None}),
        )

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            result = await settle_card(ride, RIDE_ID, RIDER_ID, Decimal("30.00"), Decimal("0"))

        assert result.success is False
        assert result.status_code == 402
        assert result.error_code == "no_payment_method"
        assert (result.extra or {}).get("suggested_action") == "change_card"
        assert _last(updates, "payment_status") == "pending"


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


@pytest.mark.asyncio
class TestIncrementFoldsTipIntoOneCharge:
    """The hold is now the bare fare, so ANY tip pushes the total over it.

    Rather than always paying a second Stripe fixed fee, settlement first tries
    to raise the hold to cover the tip and capture once. These tests pin that
    routing, including both fallbacks — the capability being absent, and the
    issuer refusing the increase.
    """

    def _exact_fare_ride(self, incrementable):
        # Hold == fare, which is what the zero-buffer booking path now produces.
        r = _held_ride(authorized_amount="25.00")
        r["auth_incrementable"] = incrementable
        return r

    async def test_incrementable_hold_settles_as_one_capture(self):
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        inc = _outcome(status="authorized", payment_intent_id="pi_hold", charged_amount=Decimal("27.00"))
        cap = _outcome(status="captured", payment_intent_id="pi_hold", charged_amount=Decimal("27.00"))
        charge_mock = AsyncMock()
        patches, updates = _common_patches(capture=cap)
        ps = "backend.services.payment_service."
        inc_mock = AsyncMock(return_value=inc)

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(patch(ps + "increment_authorization", inc_mock))
            st.enter_context(patch(ps + "charge_ride", charge_mock))
            result = await settle_card(
                self._exact_fare_ride(True), RIDE_ID, RIDER_ID, Decimal("27.00"), Decimal("2.00")
            )

        assert result.success is True
        inc_mock.assert_awaited_once()
        assert inc_mock.call_args.kwargs["new_total"] == Decimal("27.00")
        # One fee: no second PaymentIntent for the tip.
        charge_mock.assert_not_awaited()
        assert _last(updates, "payment_status") == "paid"

    async def test_non_incrementable_card_uses_two_charges(self):
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        cap = _outcome(status="captured", payment_intent_id="pi_hold", charged_amount=Decimal("25.00"))
        over = _outcome(status="succeeded", payment_intent_id="pi_tip", charged_amount=Decimal("2.00"))
        patches, updates = _common_patches(capture=cap, charge=over)
        ps = "backend.services.payment_service."
        inc_mock = AsyncMock()

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(patch(ps + "increment_authorization", inc_mock))
            result = await settle_card(
                self._exact_fare_ride(False), RIDE_ID, RIDER_ID, Decimal("27.00"), Decimal("2.00")
            )

        assert result.success is True
        # Gated on the stored capability — no doomed Stripe round-trip.
        inc_mock.assert_not_awaited()
        assert _last(updates, "payment_status") == "paid"

    async def test_declined_increment_falls_back_to_two_charges(self):
        """Issuer refused the extra amount; the original hold must still settle."""
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        inc = _outcome(status="declined", decline_code="insufficient_funds")
        cap = _outcome(status="captured", payment_intent_id="pi_hold", charged_amount=Decimal("25.00"))
        over = _outcome(status="succeeded", payment_intent_id="pi_tip", charged_amount=Decimal("2.00"))
        patches, updates = _common_patches(capture=cap, charge=over)
        ps = "backend.services.payment_service."
        inc_mock = AsyncMock(return_value=inc)

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(patch(ps + "increment_authorization", inc_mock))
            result = await settle_card(
                self._exact_fare_ride(True), RIDE_ID, RIDER_ID, Decimal("27.00"), Decimal("2.00")
            )

        assert result.success is True
        inc_mock.assert_awaited_once()
        # A failed increment leaves the original hold intact, so the fare still
        # captures and only the tip needs a second charge.
        assert _last(updates, "payment_status") == "paid"


@pytest.mark.asyncio
class TestUncapturableHoldIsReleased:
    """A capture failure sends the caller off to mint a NEW PaymentIntent and
    repoint rides.payment_intent_id at it — which is the only durable reference
    to the original hold. Without an explicit release the hold is unreachable
    and ties up the rider's funds until Stripe's ~7-day expiry.

    The gap predates this work for the fare-only case, but a successful
    increment makes the abandoned hold larger (fare + tip)."""

    async def test_failed_capture_releases_the_hold_before_falling_back(self):
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        cap = _outcome(status="failed", error_message="hold expired")
        fresh = _outcome(status="succeeded", payment_intent_id="pi_fresh", charged_amount=Decimal("30.00"))
        patches, _ = _common_patches(capture=cap, charge=fresh)
        ps = "backend.services.payment_service."
        release = AsyncMock(return_value=True)

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(patch(ps + "cancel_authorization", release))
            result = await settle_card(_held_ride(), RIDE_ID, RIDER_ID, Decimal("30.00"), Decimal("5.00"))

        assert result.success is True
        release.assert_awaited_once()
        assert release.call_args.kwargs["payment_intent_id"] == "pi_hold"

    async def test_a_failed_release_still_settles_the_ride(self):
        """Releasing is best-effort — it must never block the charge that
        actually collects the fare."""
        from contextlib import ExitStack

        from backend.services.payment_service import settle_card

        cap = _outcome(status="failed", error_message="hold expired")
        fresh = _outcome(status="succeeded", payment_intent_id="pi_fresh", charged_amount=Decimal("30.00"))
        patches, updates = _common_patches(capture=cap, charge=fresh)
        ps = "backend.services.payment_service."

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(patch(ps + "cancel_authorization", AsyncMock(side_effect=RuntimeError("stripe down"))))
            result = await settle_card(_held_ride(), RIDE_ID, RIDER_ID, Decimal("30.00"), Decimal("5.00"))

        assert result.success is True
        assert _last(updates, "payment_status") == "paid"
