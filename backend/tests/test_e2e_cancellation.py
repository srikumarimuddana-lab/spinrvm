"""
E2E — Ride cancellation scenarios.

Covers every valid and invalid cancellation path for both rider and driver:

  Rider cancel (rides.py cancel_ride_rider):
    • searching state  → cancelled, no fee, driver freed
    • driver_arrived   → cancelled, $5.00 driver fee + $0.50 admin fee
    • in_progress      → 409 (cannot cancel after trip starts)
    • completed        → 409

  Driver cancel (drivers.py cancel_ride):
    • driver_accepted  → cancelled, rider receives WS ride_cancelled event

Marked ``e2e`` so CI can run `pytest -m e2e` independently.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

RIDER_ID = "rider_cancel_e2e"
DRIVER_USER_ID = "driver_user_cancel_e2e"
DRIVER_ID = "driver_cancel_e2e"
RIDE_ID = "ride_cancel_e2e_001"


def _ride(status: str, driver_id: str | None = None, **extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": driver_id,
        "status": status,
        "total_fare": 18.5,
        "tip_amount": 0,
        "driver_earnings": 16.0,
        "payment_method": "card",
        "driver_accepted_at": None,
    }
    row.update(extra)
    return row


def _driver() -> dict:
    return {"id": DRIVER_ID, "user_id": DRIVER_USER_ID, "name": "Test Driver"}


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRiderCancelSearching:
    """Rider cancels while no driver is assigned — no fee, ride flips to cancelled."""

    async def test_searching_cancel_succeeds_no_fee(self):
        from backend.routes import rides as rides_mod

        ride_searching = _ride(status="searching")
        ride_cancelled = _ride(status="cancelled")

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride_searching, None])),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            result = await fn(
                request=MagicMock(),
                ride_id=RIDE_ID,
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        # No driver assigned → both fees should be zero
        assert result["cancellation_fee"] == 0.0

    async def test_searching_cancel_broadcasts_status(self):
        """broadcast_ride_status must be called with 'cancelled'."""
        from backend.routes import rides as rides_mod

        ride_searching = _ride(status="searching")
        ride_cancelled = _ride(status="cancelled")

        broadcast_mock = AsyncMock()
        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride_searching, None])),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_ride_status", broadcast_mock),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            await fn(request=MagicMock(), ride_id=RIDE_ID, current_user={"id": RIDER_ID})

        broadcast_mock.assert_awaited_once()
        args = broadcast_mock.call_args
        assert args[0][1] == "cancelled"


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRiderCancelDriverArrived:
    """Rider cancels after driver arrives — $5 flat fee to driver, $0.50 to admin."""

    async def test_driver_arrived_applies_flat_cancellation_fee(self):
        from backend.routes import rides as rides_mod

        ride_arrived = _ride(
            status="driver_arrived",
            driver_id=DRIVER_ID,
            driver_accepted_at=None,
        )
        driver = _driver()
        ride_cancelled = _ride(status="cancelled", driver_id=DRIVER_ID)

        settings = {"cancellation_fee_admin": 0.50, "cancellation_fee_driver": 2.50}

        wallet = {"id": "wallet_1", "balance": 100.0}

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride_arrived, None, wallet])),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value=settings)),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.rides._deps.db.update_one", AsyncMock()),
            patch("backend.routes.rides._deps.db.insert_one", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            result = await fn(
                request=MagicMock(),
                ride_id=RIDE_ID,
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        # driver_arrived flat fee: $5.00 driver + $0.50 admin
        assert result["cancellation_fee"] == 5.50

    async def test_driver_arrived_notifies_driver_via_ws(self):
        from backend.routes import rides as rides_mod

        ride_arrived = _ride(status="driver_arrived", driver_id=DRIVER_ID)
        driver = _driver()
        ride_cancelled = _ride(status="cancelled", driver_id=DRIVER_ID)
        settings = {"cancellation_fee_admin": 0.50, "cancellation_fee_driver": 2.50}
        wallet = {"id": "wallet_1", "balance": 100.0}

        ws_mock = AsyncMock()
        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride_arrived, None, wallet])),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value=settings)),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.rides._deps.db.update_one", AsyncMock()),
            patch("backend.routes.rides._deps.db.insert_one", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides._deps.manager.send_personal_message", ws_mock),
            patch("backend.routes.rides._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            await fn(request=MagicMock(), ride_id=RIDE_ID, current_user={"id": RIDER_ID})

        # Driver must receive a ride_cancelled WS event
        sent_events = [call.args[0] for call in ws_mock.call_args_list]
        assert any(e.get("type") == "ride_cancelled" for e in sent_events)


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRiderCancelIllegalStates:
    """Cancel from in_progress or completed must raise 409."""

    @pytest.mark.parametrize("status", ["in_progress", "completed"])
    async def test_cancel_illegal_state_raises_409(self, status: str):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.utils.error_handling import SpinrException

        # First find_one (state-filter) returns None (ride not in allowed states).
        # Second find_one (existence check) returns the ride in its actual state.
        ride_actual = _ride(status=status)

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[None, ride_actual])),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await fn(
                    request=MagicMock(),
                    ride_id=RIDE_ID,
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 409
        text = getattr(exc_info.value, "detail", None) or getattr(exc_info.value, "message", "")
        assert status in str(text)

    async def test_cancel_unknown_ride_raises_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.utils.error_handling import SpinrException

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await fn(
                    request=MagicMock(),
                    ride_id="nonexistent_ride",
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 404


@pytest.mark.e2e
@pytest.mark.asyncio
class TestDriverCancelNotifiesRider:
    """Driver cancels from driver_accepted — rider receives WS ride_cancelled event."""

    async def test_driver_cancel_sends_ws_to_rider(self):
        from backend.routes import drivers as drv_mod

        ride_accepted = {
            "id": RIDE_ID,
            "rider_id": RIDER_ID,
            "driver_id": DRIVER_ID,
            "status": "driver_accepted",
        }
        driver = _driver()

        ws_mock = AsyncMock()
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride_accepted)),
            patch("backend.routes.drivers._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", ws_mock),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            await drv_mod.cancel_ride(
                ride_id=RIDE_ID,
                reason="driver_personal",
                current_user={"id": DRIVER_USER_ID},
            )

        # Rider channel (rider_{rider_id}) must receive the cancellation event
        rider_channel = f"rider_{RIDER_ID}"
        events_to_rider = [call.args[0] for call in ws_mock.call_args_list if call.args[1] == rider_channel]
        assert any(e.get("type") == "ride_cancelled" for e in events_to_rider), (
            "Driver cancel must push ride_cancelled to the rider WS channel"
        )


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRiderCancelFeeWriteFailureReleasesDriver:
    """Regression: a fee-write failure after the cancel is claimed must NOT
    strand the assigned driver. The cancel is already persisted by the atomic
    claim, so set_driver_available / the Period-1 insurance transition / the
    driver notification must still run even when the wallet write raises."""

    async def test_wallet_write_failure_still_releases_and_notifies_driver(self):
        from backend.routes import rides as rides_mod

        ride_arrived = _ride(
            status="driver_arrived",
            driver_id=DRIVER_ID,
            payment_method="wallet",
            service_area_id=None,
        )
        ride_cancelled = _ride(status="cancelled", driver_id=DRIVER_ID)
        driver = _driver()
        settings = {"cancellation_fee_admin": 0.50, "cancellation_fee_driver": 2.50}
        wallet = {"id": "wallet_1", "balance": 100.0}

        set_avail = AsyncMock()
        period = AsyncMock()
        ws = AsyncMock()

        with (
            # require-state check → ride; then the wallet lookup → wallet
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride_arrived, wallet])),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value=settings)),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            # claim + wallet-balance update both go through update_one and succeed
            patch("backend.routes.rides._deps.db.update_one", AsyncMock()),
            # the wallet_transactions insert is the fee write that fails
            patch("backend.routes.rides._deps.db.insert_one", AsyncMock(side_effect=RuntimeError("db down"))),
            patch("backend.routes.rides._deps.pay_driver_cancellation_fee", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides._deps.db_supabase.set_driver_available", set_avail),
            patch("backend.routes.rides._deps.record_period_transition", period),
            patch("backend.routes.rides._deps.manager.send_personal_message", ws),
            patch("backend.routes.rides._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            result = await fn(
                request=MagicMock(),
                ride_id=RIDE_ID,
                current_user={"id": RIDER_ID},
            )

        # The cancel still succeeds (the ride is already persisted as cancelled).
        assert result["success"] is True
        # Driver is released back to available despite the fee-write failure.
        set_avail.assert_any_await(DRIVER_ID, True)
        # SGI Period-1 transition is recorded for the released driver.
        period.assert_any_await(DRIVER_ID, 1)
        # Driver is notified of the cancellation.
        driver_channel = f"driver_{DRIVER_USER_ID}"
        notified = [
            call.args[0]
            for call in ws.call_args_list
            if len(call.args) > 1 and call.args[1] == driver_channel
        ]
        assert any(e.get("type") == "ride_cancelled" for e in notified), (
            "Driver must be notified of the cancel even when the fee write fails"
        )
