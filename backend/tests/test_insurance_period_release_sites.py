"""HIGH #2 (2026-09-22 insurance-period audit): ride-end paths that released
the driver and then wrote Period 1 unconditionally, ignoring the clamped row
``set_driver_available`` returned.

A driver forced offline mid-ride (admin suspend, document expiry) still holds
an open Period 2/3 — the forced-offline close deliberately leaves it open —
so the ride-end path is what must close it, to Period 0. Writing Period 1
there asserts TNC contingent cover over personal-auto time, and nothing ever
closes that row (the reconciler only scans online drivers).

Sites covered here: driver ``cancel_ride`` and rider-side ``complete_ride``
(``routes/rides/lifecycle.py``). Sibling coverage lives next to each path's
existing tests: driver ``complete_ride`` in ``test_ride_complete_coverage.py``,
``mark_rider_noshow`` in ``test_c2_driver_cancel_atomic.py``, admin cancel /
force-complete in ``test_admin_rides_cancel_state.py``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import insurance_periods

_OFFLINE = {"id": "drv-1", "is_online": False, "is_available": False}
_ONLINE = {"id": "drv-1", "is_online": True, "is_available": True}


# ---------------------------------------------------------------------------
# routes/drivers/ride_cancel.py cancel_ride
# ---------------------------------------------------------------------------


def _run_driver_cancel(released_row, ride_status="driver_accepted"):
    from backend.routes import drivers as drv

    driver = {"id": "drv-1", "user_id": "user-1"}
    ride = {"id": "ride-1", "status": ride_status, "rider_id": "rider-1", "driver_id": "drv-1"}
    record = AsyncMock()
    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value={"id": "ride-1"})),
        patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock(return_value=released_row)),
        # Both the legacy direct call and the shared helper's call are captured,
        # so the old unconditional record(…, 1) would show up here too.
        patch("backend.routes.drivers._deps.record_period_transition", record),
        patch.object(insurance_periods, "record_period_transition", record),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
        patch("backend.routes.drivers._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        result = asyncio.run(drv.cancel_ride(ride_id="ride-1", reason="", request=None, current_user={"id": "user-1"}))
    assert result == {"success": True}
    return record


def test_driver_cancel_offline_driver_closes_to_period_0_not_1():
    """Driver was forced offline (admin suspend) mid-assignment, then cancels.
    Old code: record_period_transition(drv, 1) unconditionally — fails here."""
    record = _run_driver_cancel(_OFFLINE)
    record.assert_awaited_once_with("drv-1", 0)


def test_driver_cancel_online_driver_still_closes_to_period_1():
    """Guard against over-suppression: the normal case is unchanged."""
    record = _run_driver_cancel(_ONLINE)
    record.assert_awaited_once_with("drv-1", 1)


# ---------------------------------------------------------------------------
# routes/rides/lifecycle.py rider_complete_ride
# ---------------------------------------------------------------------------


def _run_rider_complete(released_row):
    from backend.routes.rides import rider_complete_ride

    ride = {
        "id": "ride-1",
        "status": "in_progress",
        "rider_id": "rider-1",
        "driver_id": "drv-1",
        "total_fare": 10,
    }
    completed = {**ride, "status": "completed"}
    record = AsyncMock()
    with (
        patch("backend.routes.rides._deps.db_supabase") as mock_db,
        patch("backend.routes.rides._deps.record_period_transition", record),
        patch.object(insurance_periods, "record_period_transition", record),
    ):
        mock_db.get_ride = AsyncMock(side_effect=[ride, completed])
        mock_db.update_ride = AsyncMock()
        mock_db.update_one = AsyncMock(return_value=ride)
        mock_db.set_driver_available = AsyncMock(return_value=released_row)
        mock_db.get_driver_by_id = AsyncMock(return_value={"id": "drv-1", "user_id": "driver-user-1"})
        result = asyncio.run(rider_complete_ride(ride_id="ride-1", current_user={"id": "rider-1"}))
    assert result["status"] == "completed"
    return record, mock_db


def test_rider_complete_offline_driver_closes_period_3_to_period_0():
    record, _ = _run_rider_complete(_OFFLINE)
    record.assert_awaited_once_with("drv-1", 0)


def test_rider_complete_online_driver_closes_period_3_to_period_1():
    record, mock_db = _run_rider_complete(_ONLINE)
    record.assert_awaited_once_with("drv-1", 1)
    mock_db.set_driver_available.assert_awaited_once_with("drv-1", available=True, total_rides_inc=1)


@pytest.mark.parametrize("released", [None, "not-a-row"])
def test_rider_complete_unreadable_release_writes_no_period(released):
    """No row back means online state is unknown — write nothing rather than
    guess (same contract as release_driver_and_close_period)."""
    record, _ = _run_rider_complete(released)
    record.assert_not_awaited()
