"""Card riders must actually be charged the no-show fee (audit N2).

`mark_rider_noshow` used to have a wallet branch and nothing else:

    if payment_method == "wallet":
        ... wallet_apply_delta(-total_fee) ...
    # (no card branch)

...while `pay_driver_cancellation_fee` below it credited the driver the
$4.00 driver share regardless. So every card no-show was platform-funded —
$4.00 paid out, $4.50 never collected — and the ride was stamped with
`cancellation_fee_admin`/`cancellation_fee_driver` as if it had been. The
booking hold was neither captured nor released either, so the rider's card
stayed blocked until Stripe's ~7-day expiry.

These tests pin the card collection (hold capture first, fresh charge as the
fallback), the no-fee hold release, and that the wallet path is untouched.

Patch-target conventions: see the docstring of test_driver_ride_flow_coverage.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_FEE_ADMIN = Decimal("0.50")
_FEE_DRIVER = Decimal("4.00")
_TOTAL_FEE = _FEE_ADMIN + _FEE_DRIVER


def _driver():
    return {"id": "drv-1", "user_id": "user-1"}


def _ride(**kw):
    base = {
        "id": "ride-1",
        "status": "driver_arrived",
        "driver_id": "drv-1",
        "rider_id": "rider-1",
        "driver_arrived_at": (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat(),
        "service_area_id": None,
        "payment_method": "card",
        "payment_intent_id": "pi_booking_1",
        "auth_status": "authorized",
        "authorized_amount": Decimal("30.00"),
    }
    base.update(kw)
    return base


def _outcome(status, *, charged=None, pi="pi_fee_1"):
    return SimpleNamespace(status=status, charged_amount=charged, payment_intent_id=pi)


def _run(ride, *, fee=(_FEE_ADMIN, _FEE_DRIVER), extra=(), **patches):
    """Drive mark_rider_noshow with everything downstream of the fee stubbed."""
    from backend.routes import drivers as drv

    stack = [
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={"noshow_wait_seconds": 300})),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value={"id": "ride-1"})),
        patch("backend.routes.drivers._deps.db_supabase.update_ride", AsyncMock(return_value={"id": "ride-1"})),
        patch(
            "backend.services.cancellation_service.calculate_noshow_fee",
            MagicMock(return_value=fee),
        ),
        patch("backend.services.cancellation_service.pay_driver_cancellation_fee", AsyncMock()),
        patch(
            "backend.routes.drivers._deps.db_supabase.set_driver_available",
            AsyncMock(return_value={"id": "drv-1", "is_available": True}),
        ),
        patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
        patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.drivers._deps.spawn", side_effect=lambda c: c.close()),
        patch("backend.services.ledger_service.record_event", AsyncMock()),
        patch(
            "backend.routes.drivers._deps.db_supabase.get_user_by_id",
            AsyncMock(return_value={"stripe_customer_id": "cus_1", "default_payment_method": "pm_1"}),
        ),
        patch("backend.routes.drivers._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        *extra,
    ]
    mocks = {}
    with __import__("contextlib").ExitStack() as es:
        for cm in stack:
            es.enter_context(cm)
        for name, cm in patches.items():
            mocks[name] = es.enter_context(cm)
        asyncio.run(drv.mark_rider_noshow(ride_id="ride-1", current_user={"id": "user-1"}))
    return mocks


class TestCardNoShowFeeIsCollected:
    def test_fee_is_captured_from_the_booking_hold(self):
        """Preferred path: the funds are already reserved, so this fee cannot
        be declined for insufficient funds."""
        capture = AsyncMock(return_value=_outcome("captured", charged=_TOTAL_FEE, pi="pi_booking_1"))
        fresh = AsyncMock()
        mocks = _run(
            _ride(),
            capture=patch("backend.utils.stripe_charge.capture_cancellation_fee", capture),
            fresh=patch("backend.utils.stripe_charge.charge_ancillary_fee", fresh),
        )
        mocks["capture"].assert_awaited_once()
        kwargs = mocks["capture"].await_args.kwargs
        assert kwargs["payment_intent_id"] == "pi_booking_1"
        assert kwargs["fee"] == _TOTAL_FEE
        assert kwargs["authorized_amount"] == Decimal("30.00")
        # Hold capture succeeded -> no second charge against the same card.
        mocks["fresh"].assert_not_awaited()

    def test_falls_back_to_a_fresh_charge_when_there_is_no_live_hold(self):
        capture = AsyncMock()
        fresh = AsyncMock(return_value=_outcome("succeeded", charged=_TOTAL_FEE))
        mocks = _run(
            _ride(payment_intent_id=None, auth_status=None, authorized_amount=0),
            capture=patch("backend.utils.stripe_charge.capture_cancellation_fee", capture),
            fresh=patch("backend.utils.stripe_charge.charge_ancillary_fee", fresh),
        )
        mocks["capture"].assert_not_awaited()
        mocks["fresh"].assert_awaited_once()
        kwargs = mocks["fresh"].await_args.kwargs
        assert kwargs["amount"] == _TOTAL_FEE
        assert kwargs["rider_id"] == "rider-1"
        # Distinct fee_type -> distinct Stripe idempotency key from a
        # cancellation fee on the same ride (see utils/stripe_charge.py:376).
        assert kwargs["fee_type"] == "noshow_fee"

    def test_falls_back_to_a_fresh_charge_when_the_capture_fails(self):
        """A failed capture must not release the hold — the fee is still owed
        and the fresh charge is the only remaining path to it."""
        capture = AsyncMock(return_value=_outcome("failed"))
        fresh = AsyncMock(return_value=_outcome("succeeded", charged=_TOTAL_FEE))
        release = AsyncMock()
        mocks = _run(
            _ride(),
            capture=patch("backend.utils.stripe_charge.capture_cancellation_fee", capture),
            fresh=patch("backend.utils.stripe_charge.charge_ancillary_fee", fresh),
            release=patch("backend.routes.drivers._deps.cancel_authorization", release),
        )
        mocks["capture"].assert_awaited_once()
        mocks["fresh"].assert_awaited_once()
        mocks["release"].assert_not_awaited()

    def test_ride_records_the_fee_payment_intent_not_the_booking_one(self):
        """WS-8/migration 251: the booking PI must stay auditable, so the fee
        PI goes in its own column."""
        upd = AsyncMock(return_value={"id": "ride-1"})
        mocks = _run(
            _ride(payment_intent_id=None, auth_status=None, authorized_amount=0),
            upd=patch("backend.routes.drivers._deps.db_supabase.update_ride", upd),
            fresh=patch(
                "backend.utils.stripe_charge.charge_ancillary_fee",
                AsyncMock(return_value=_outcome("succeeded", charged=_TOTAL_FEE, pi="pi_fee_9")),
            ),
        )
        payloads = [c.args[1] for c in mocks["upd"].await_args_list if len(c.args) > 1]
        fee_write = next(p for p in payloads if "cancellation_fee_driver" in p)
        assert fee_write["cancel_fee_payment_intent_id"] == "pi_fee_9"
        assert fee_write["payment_status"] == "paid"
        assert "payment_intent_id" not in fee_write

    def test_uncollected_fee_is_logged_at_error_and_driver_is_still_paid(self):
        """A decline must surface loudly (payment path — never a warning), and
        must not change the existing policy of paying the driver."""
        pay_driver = AsyncMock()
        mocks = _run(
            _ride(payment_intent_id=None, auth_status=None, authorized_amount=0),
            fresh=patch(
                "backend.utils.stripe_charge.charge_ancillary_fee",
                AsyncMock(return_value=_outcome("declined", pi="pi_dec")),
            ),
            pay_driver=patch("backend.services.cancellation_service.pay_driver_cancellation_fee", pay_driver),
            log=patch("backend.routes.drivers.ride_cancel.logger.error", MagicMock()),
        )
        mocks["pay_driver"].assert_awaited_once()
        assert any("uncollected" in str(c) for c in mocks["log"].call_args_list)

    def test_stripe_unconfigured_is_not_recorded_as_a_decline(self):
        """Dev/test with no Stripe: no charge was attempted at all, so the ride
        must not be stamped with a payment_status."""
        upd = AsyncMock(return_value={"id": "ride-1"})
        mocks = _run(
            _ride(payment_intent_id=None, auth_status=None, authorized_amount=0),
            upd=patch("backend.routes.drivers._deps.db_supabase.update_ride", upd),
            fresh=patch(
                "backend.utils.stripe_charge.charge_ancillary_fee",
                AsyncMock(return_value=_outcome("unconfigured", pi=None)),
            ),
        )
        payloads = [c.args[1] for c in mocks["upd"].await_args_list if len(c.args) > 1]
        fee_write = next(p for p in payloads if "cancellation_fee_driver" in p)
        assert "payment_status" not in fee_write
        assert "cancel_fee_payment_intent_id" not in fee_write


class TestNoShowHoldRelease:
    def test_zero_fee_releases_the_booking_hold(self):
        """No fee owed but a live hold: release it rather than leaving the
        rider's card blocked until Stripe's ~7-day expiry."""
        release = AsyncMock(return_value=True)
        mocks = _run(
            _ride(),
            fee=(Decimal("0"), Decimal("0")),
            release=patch("backend.routes.drivers._deps.cancel_authorization", release),
        )
        mocks["release"].assert_awaited_once()
        assert mocks["release"].await_args.kwargs["payment_intent_id"] == "pi_booking_1"

    def test_zero_fee_without_a_hold_releases_nothing(self):
        release = AsyncMock()
        mocks = _run(
            _ride(payment_intent_id=None, auth_status=None),
            fee=(Decimal("0"), Decimal("0")),
            release=patch("backend.routes.drivers._deps.cancel_authorization", release),
        )
        mocks["release"].assert_not_awaited()


class TestWalletPathUnchanged:
    def test_wallet_rider_still_debited_and_no_card_call_made(self):
        wallet_delta = AsyncMock(return_value={"applied_delta": -_TOTAL_FEE})
        capture = AsyncMock()
        fresh = AsyncMock()
        mocks = _run(
            _ride(payment_method="wallet"),
            extra=[
                patch(
                    "backend.routes.drivers._deps.db_supabase.find_one",
                    AsyncMock(return_value={"id": "wal-1"}),
                )
            ],
            wallet=patch("backend.routes.drivers._deps.db_supabase.wallet_apply_delta", wallet_delta),
            capture=patch("backend.utils.stripe_charge.capture_cancellation_fee", capture),
            fresh=patch("backend.utils.stripe_charge.charge_ancillary_fee", fresh),
        )
        mocks["wallet"].assert_awaited_once()
        assert mocks["wallet"].await_args.kwargs["delta"] == -_TOTAL_FEE
        assert mocks["wallet"].await_args.kwargs["reference_id"] == "ride-1"
        mocks["capture"].assert_not_awaited()
        mocks["fresh"].assert_not_awaited()
