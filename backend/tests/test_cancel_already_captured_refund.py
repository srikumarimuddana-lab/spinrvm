"""
Cancellation of a ride whose booking-time hold is ALREADY captured.

2026-09-21 fix: a real test ride showed a rider cancelling a scheduled ride
for a computed $0 fee, ~26 minutes after booking and well before any driver
arrived — but the fare had already been fully captured (auth_status
"captured", payment_status "paid") with nothing refunded. Root cause: the
payment-retry loop's requires_capture branch can auto-capture a booking
hold before a ride completes (fixed separately, see
docs/change-log/2026-09-21-payment-retry-requires-capture-pre-trip-guard.md),
and cancellation.py's hold-handling only ever acted on a LIVE hold
(`_hold_is_live`, auth_status in "authorized"/"fare_only") — once
auth_status was already "captured", the whole block was skipped and there
was no refund path anywhere in this file.

These tests pin the new `elif _auth == "captured"` branch: it must refund
whatever was captured beyond the fee actually owed, never double-charge a
fee that's already covered by the capture, and never silently swallow a
failed refund attempt.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import ChargeOutcome

RIDER_ID = "rider_captured_cancel"
DRIVER_USER_ID = "driver_user_captured_cancel"
DRIVER_ID = "driver_captured_cancel"
RIDE_ID = "ride_captured_cancel_001"

NO_FEE_SETTINGS = {"cancellation_fee_admin": 0.0, "cancellation_fee_driver": 0.0}
FEE_SETTINGS = {"cancellation_fee_admin": 0.50, "cancellation_fee_driver": 1.50}


def _ride(status: str, **extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
        "payment_method": "card",
        "payment_method_id": "pm_test_123",
        "driver_accepted_at": None,
        "payment_intent_id": "pi_already_captured",
        # The hold is ALREADY captured — not a live "authorized"/"fare_only"
        # hold. This is the case _hold_is_live does not cover.
        "auth_status": "captured",
        "authorized_amount": 25.00,
        "payment_status": "paid",
    }
    row.update(extra)
    return row


def _base_patches(settings=None):
    return (
        patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=_ride("driver_arrived"))),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value=settings or NO_FEE_SETTINGS)),
        patch(
            "backend.routes.rides._deps.db_supabase.get_user_by_id",
            AsyncMock(return_value={"id": RIDER_ID, "stripe_customer_id": "cus_test_123"}),
        ),
        patch(
            "backend.routes.rides._deps.db_supabase.get_driver_by_id",
            AsyncMock(return_value={"id": DRIVER_ID, "user_id": DRIVER_USER_ID, "name": "T"}),
        ),
        patch("backend.routes.rides._deps.db.update_one", AsyncMock()),
        patch("backend.routes.rides._deps.db.insert_one", AsyncMock()),
        patch("backend.routes.rides._deps.record_ledger_event", AsyncMock(return_value="evt_1")),
        patch("backend.routes.rides._deps.record_refund_event", AsyncMock(return_value="evt_refund_1")),
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride("cancelled"))),
        patch("backend.routes.rides._deps.db_supabase.set_driver_available", AsyncMock()),
        patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides._deps.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        # Live-hold helpers must never fire from this branch.
        patch("backend.routes.rides._deps.capture_cancellation_fee", AsyncMock()),
        patch("backend.routes.rides._deps.cancel_authorization", AsyncMock()),
    )


@contextmanager
def _patch_all(*extra, settings=None):
    with ExitStack() as stack:
        for p in _base_patches(settings):
            stack.enter_context(p)
        for p in extra:
            stack.enter_context(p)
        yield


async def _run_cancel():
    from backend.routes import rides as rides_mod

    fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
    return await fn(request=MagicMock(), ride_id=RIDE_ID, reason="", current_user={"id": RIDER_ID})


@pytest.mark.e2e
@pytest.mark.asyncio
class TestAlreadyCapturedHoldRefund:
    async def test_zero_fee_refunds_full_captured_amount(self):
        update_ride_mock = AsyncMock()
        refund_mock = AsyncMock(
            return_value=ChargeOutcome(
                status="refunded", payment_intent_id="pi_already_captured", charged_amount=Decimal("2.10")
            )
        )
        charge_mock = AsyncMock()

        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.refund_excess_capture", refund_mock),
            patch("backend.routes.rides._deps.charge_ancillary_fee", charge_mock),
        ):
            result = await _run_cancel()

        assert result["success"] is True
        refund_mock.assert_awaited_once()
        kwargs = refund_mock.call_args.kwargs
        assert kwargs["payment_intent_id"] == "pi_already_captured"
        assert kwargs["fee_owed"] == Decimal("0")

        # No fresh charge on top of an already-captured, now-refunded hold.
        charge_mock.assert_not_awaited()

        written = update_ride_mock.call_args_list[0].args[1]
        assert written["payment_status"] == "refunded"
        assert Decimal(str(written["refund_amount"])) == Decimal("2.10")

    async def test_partial_fee_refunds_only_the_excess(self):
        """A cancellation fee IS owed — only the amount above it comes back."""
        update_ride_mock = AsyncMock()
        refund_mock = AsyncMock(
            return_value=ChargeOutcome(
                status="refunded", payment_intent_id="pi_already_captured", charged_amount=Decimal("1.60")
            )
        )
        charge_mock = AsyncMock()

        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.refund_excess_capture", refund_mock),
            patch("backend.routes.rides._deps.charge_ancillary_fee", charge_mock),
            settings=FEE_SETTINGS,
        ):
            result = await _run_cancel()

        assert result["success"] is True
        refund_mock.assert_awaited_once()
        assert refund_mock.call_args.kwargs["fee_owed"] == Decimal("2.00")
        charge_mock.assert_not_awaited()

        written = update_ride_mock.call_args_list[0].args[1]
        assert written["payment_status"] == "partially_refunded"
        assert Decimal(str(written["refund_amount"])) == Decimal("1.60")

    async def test_capture_already_covers_the_fee_no_refund_needed(self):
        """The captured amount is <= the fee owed — nothing to give back, and
        the fresh-charge fallback must not double-bill the fee on top of the
        already-captured money."""
        update_ride_mock = AsyncMock()
        refund_mock = AsyncMock(return_value=ChargeOutcome(status="not_needed", charged_amount=Decimal("0.00")))
        charge_mock = AsyncMock()

        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.refund_excess_capture", refund_mock),
            patch("backend.routes.rides._deps.charge_ancillary_fee", charge_mock),
            settings=FEE_SETTINGS,
        ):
            result = await _run_cancel()

        assert result["success"] is True
        charge_mock.assert_not_awaited()

        written = update_ride_mock.call_args_list[0].args[1]
        assert "refund_amount" not in written
        assert written.get("payment_status") != "refunded"

    async def test_refund_failure_is_not_silently_swallowed_and_does_not_double_charge(self):
        """CLAUDE.md: never silently swallow a payment-path failure. A failed
        refund must be logged loudly, must not be recorded as refund_amount,
        and must not fall through to a fresh fee charge on top of money that
        is still sitting captured and un-refunded."""
        update_ride_mock = AsyncMock()
        refund_mock = AsyncMock(return_value=ChargeOutcome(status="failed", error_message="stripe down"))
        charge_mock = AsyncMock()

        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.refund_excess_capture", refund_mock),
            patch("backend.routes.rides._deps.charge_ancillary_fee", charge_mock),
        ):
            result = await _run_cancel()

        assert result["success"] is True
        charge_mock.assert_not_awaited()

        written = update_ride_mock.call_args_list[0].args[1]
        assert "refund_amount" not in written
        assert written.get("payment_status") != "refunded"

    async def test_pending_refund_is_exposed_without_finalizing_refund_accounting(self):
        update_ride_mock = AsyncMock()
        refund_mock = AsyncMock(return_value=ChargeOutcome(
            status="pending", payment_intent_id="pi_already_captured",
            charged_amount=Decimal("2.10"),
            raw={"refund_id": "re_pending_1", "refund_status": "pending"},
        ))
        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.refund_excess_capture", refund_mock),
            patch("backend.routes.rides._deps.charge_ancillary_fee", AsyncMock()),
        ):
            await _run_cancel()

        written = update_ride_mock.call_args_list[0].args[1]
        assert written["refund_status"] == "pending"
        assert written["refund_id"] == "re_pending_1"
        assert "refund_amount" not in written
        assert written.get("payment_status") != "refunded"

    @pytest.mark.parametrize(
        "refund_outcome",
        [
            ChargeOutcome(status="failed", error_message="stripe unavailable"),
            None,  # Stripe may have accepted the refund even though its response was lost.
        ],
        ids=["definite-failure", "ambiguous-response"],
    )
    async def test_nonzero_fee_is_not_charged_while_captured_refund_is_unresolved(self, refund_outcome):
        update_ride_mock = AsyncMock()
        refund_mock = AsyncMock(return_value=refund_outcome)
        charge_mock = AsyncMock()
        set_available = AsyncMock()
        notify_driver = AsyncMock()

        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.refund_excess_capture", refund_mock),
            patch("backend.routes.rides._deps.charge_ancillary_fee", charge_mock),
            patch("backend.routes.rides._deps.db_supabase.set_driver_available", set_available),
            patch("backend.routes.rides._deps.manager.send_personal_message", notify_driver),
            settings=FEE_SETTINGS,
        ):
            result = await _run_cancel()

        assert result["success"] is True
        refund_mock.assert_awaited_once()
        charge_mock.assert_not_awaited()
        set_available.assert_awaited()
        notify_driver.assert_awaited()
        written = update_ride_mock.call_args_list[0].args[1]
        assert "refund_amount" not in written
        assert written.get("payment_status") != "partially_refunded"

    async def test_live_hold_never_takes_the_already_captured_path(self):
        """Sanity check: a ride with a LIVE hold (auth_status="authorized")
        must go through the existing _hold_is_live branch, never this one."""
        refund_mock = AsyncMock()
        capture_mock = AsyncMock(
            return_value=ChargeOutcome(status="captured", payment_intent_id="pi_x", charged_amount=Decimal("0.00"))
        )

        live_hold_ride = _ride("driver_arrived", auth_status="authorized")

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=live_hold_ride)),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value=NO_FEE_SETTINGS)),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": RIDER_ID, "stripe_customer_id": "cus_test_123"}),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value={"id": DRIVER_ID, "user_id": DRIVER_USER_ID, "name": "T"}),
            ),
            patch("backend.routes.rides._deps.db.update_one", AsyncMock()),
            patch("backend.routes.rides._deps.db.insert_one", AsyncMock()),
            patch("backend.routes.rides._deps.record_ledger_event", AsyncMock(return_value="evt_1")),
            patch("backend.routes.rides._deps.record_refund_event", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride("cancelled"))),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.rides._deps.capture_cancellation_fee", capture_mock),
            patch("backend.routes.rides._deps.cancel_authorization", AsyncMock(return_value=True)),
            patch("backend.routes.rides._deps.charge_ancillary_fee", AsyncMock()),
            patch("backend.routes.rides._deps.refund_excess_capture", refund_mock),
        ):
            await _run_cancel()

        refund_mock.assert_not_awaited()
