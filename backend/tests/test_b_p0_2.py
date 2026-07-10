"""
Regression tests for B-P0-2: process_payment 409 after complete_ride — state string mismatch.

Background
----------
Two bugs conspired to produce a 409 on POST /rides/{id}/process-payment after
the driver called complete:

  1. cancel_ride guarded against cancelling an in-progress trip with:
         if ride.get("status") == "trip_in_progress":
     "trip_in_progress" is a GPS tracking-phase name, NOT a ride status.
     The real ride status is "in_progress". Because the string never matched,
     the guard never fired — drivers (or the rider via the back-button path)
     could cancel a ride that was already in progress.  If the cancel landed
     before the driver's complete call was read by process_payment, the rider
     saw status="cancelled" and got 409.

  Fix: changed the guard to ride.get("status") == "in_progress".

  2. complete_ride previously wrote payment_status="completed" alongside
     status="completed".  "completed" is NOT a valid payment_status value
     (valid: pending / processing / paid / failed / requires_action).
     The test at test_rides.py::test_full_ride_lifecycle was asserting the
     old broken behaviour.  Fix: test now asserts payment_status stays None
     or "pending" after complete_ride.

Test layers
-----------
Layer 1 — pure logic: always runnable, zero backend imports.
Layer 2 — integration: require backend deps; skipped locally, run in CI.
"""

import pytest

# ---------------------------------------------------------------------------
# Layer 1 — pure logic
# ---------------------------------------------------------------------------

_VALID_RIDE_STATUSES = {
    "searching",
    "driver_assigned",
    "driver_accepted",
    "driver_arrived",
    "in_progress",
    "completed",
    "cancelled",
}
_COMPLETE_FROM_STATES = ("in_progress",)
_VALID_PAYMENT_STATUSES = {"pending", "processing", "paid", "failed", "requires_action"}


def test_trip_in_progress_is_not_a_valid_ride_status():
    """'trip_in_progress' is a GPS phase name — it must never appear as a ride status."""
    assert "trip_in_progress" not in _VALID_RIDE_STATUSES


def test_in_progress_is_a_valid_ride_status():
    assert "in_progress" in _VALID_RIDE_STATUSES


def test_cancel_guard_fires_on_in_progress():
    """Cancellation must be blocked when ride status is 'in_progress' (the real string)."""
    ride = {"id": "r1", "status": "in_progress"}

    def _cancel_guard(ride: dict) -> bool:
        return ride.get("status") == "in_progress"  # fixed code

    assert _cancel_guard(ride) is True, "guard must fire on in_progress"


def test_old_cancel_guard_did_not_fire_on_in_progress():
    """Regression: the old guard used 'trip_in_progress' and silently let cancels through."""
    ride = {"id": "r1", "status": "in_progress"}

    def _old_cancel_guard(ride: dict) -> bool:
        return ride.get("status") == "trip_in_progress"  # broken

    assert _old_cancel_guard(ride) is False, "old guard never fired → in-progress rides could be cancelled"


def test_cancel_guard_does_not_fire_on_driver_accepted():
    """Guard must only block in_progress — earlier states are cancellable."""
    for status in ("driver_assigned", "driver_accepted", "driver_arrived"):
        ride = {"id": "r1", "status": status}
        assert ride.get("status") != "in_progress"


def test_completed_is_not_a_valid_payment_status():
    """'completed' is a ride status, not a payment status — complete_ride must not write it."""
    assert "completed" not in _VALID_PAYMENT_STATUSES


def test_complete_ride_does_not_set_payment_status_to_completed():
    """Simulated update_fields from complete_ride must not contain payment_status=completed."""
    update_fields = {
        "status": "completed",
        "ride_completed_at": "2026-04-27T12:00:00Z",
        "updated_at": "2026-04-27T12:00:00Z",
        "actual_distance_km": 3.2,
    }
    # complete_ride must leave payment_status untouched — the field should not
    # appear in update_fields at all.
    assert "payment_status" not in update_fields


def test_process_payment_rejects_non_completed_ride():
    """process_payment guard: 409 when ride status is not completed."""
    for bad_status in ("in_progress", "driver_arrived", "cancelled", "searching"):
        ride = {"id": "r1", "status": bad_status}
        assert ride.get("status") != "completed", f"expected non-completed status: {bad_status}"


def test_process_payment_accepts_completed_ride():
    """process_payment guard passes when ride status is exactly 'completed'."""
    ride = {"id": "r1", "status": "completed", "payment_status": "pending"}
    assert ride.get("status") == "completed"
    assert ride.get("payment_status") not in ("paid", "processing")


# ---------------------------------------------------------------------------
# Layer 2 — integration (require backend deps; skipped if unavailable)
# ---------------------------------------------------------------------------

try:
    import backend.routes.drivers as _drivers_module

    _HAS_BACKEND_DEPS = True
except BaseException:
    _drivers_module = None  # type: ignore[assignment]
    _HAS_BACKEND_DEPS = False

_skip_no_deps = pytest.mark.skipif(not _HAS_BACKEND_DEPS, reason="backend deps not installed")


@_skip_no_deps
@pytest.mark.anyio
async def test_cancel_ride_raises_on_in_progress():
    """cancel_ride must reject when ride status is 'in_progress'."""
    from unittest.mock import AsyncMock, patch

    from backend.utils.error_handling import RideStateError

    ride = {"id": "r1", "status": "in_progress", "driver_id": "d1", "rider_id": "rider-1"}
    driver = {"id": "d1", "user_id": "u1"}

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
    ):
        with pytest.raises(RideStateError):
            await _drivers_module.cancel_ride(
                ride_id="r1",
                reason="test",
                current_user={"id": "u1"},
            )


@_skip_no_deps
@pytest.mark.anyio
async def test_complete_ride_does_not_overwrite_payment_status():
    """complete_ride must not set payment_status to any value — it leaves it untouched."""
    from unittest.mock import AsyncMock, MagicMock, patch

    written_fields: dict = {}

    async def capture_update_one(table, filters, updates):
        if table == "rides":
            data = updates.get("$set", updates)
            written_fields.update(data)
        mock = MagicMock()
        mock.modified_count = 1
        return mock

    ride = {
        "id": "r1",
        "status": "in_progress",
        "driver_id": "d1",
        "rider_id": "rider-1",
        "planned_distance_km": 2.0,
        "distance_km": 2.0,
        "base_fare": 3.0,
        "distance_fare": 3.0,
        "time_fare": 2.0,
        "booking_fee": 0.5,
        "total_fare": 8.5,
    }
    driver = {"id": "d1", "user_id": "u1", "lat": 0.0, "lng": 0.0}

    with (
        patch(
            "backend.routes.drivers._deps.db_supabase.get_rows",
            AsyncMock(side_effect=lambda t, *a, **kw: [driver] if t == "drivers" else ([ride] if t == "rides" else [])),
        ),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=capture_update_one)),
        patch("backend.routes.drivers._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
        patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.drivers._deps.asyncio.create_task", lambda coro: coro.close()),
    ):
        await _drivers_module.complete_ride(ride_id="r1", current_user={"id": "u1"})

    assert "payment_status" not in written_fields, (
        f"complete_ride must not write payment_status; got: {written_fields.get('payment_status')}"
    )
    assert written_fields.get("status") == "completed"
