"""Regression tests for C2: driver-side cancel / no-show must be atomic.

Background
----------
`cancel_ride` and `mark_rider_noshow` previously did an in-memory status check
and then wrote the cancel via `db_supabase.update_ride`, which filters on `id`
only. Between the read and the write the ride can flip to `in_progress` (rider
or driver calls verify-otp/start). The unguarded write would then overwrite
`in_progress -> cancelled`, violating "never cancel after trip start" — and for
no-show it would charge a fee on a ride that actually began.

Fix: both paths now claim the cancel with a status-guarded `update_one`
(`status IN (pre-trip states)` for cancel; `status = driver_arrived` for
no-show). A zero-row claim returns 409 and nothing is mutated/charged.

Layer 1 — pure contract (always runnable). Layer 2 — integration (CI).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Layer 1 — contract
# --------------------------------------------------------------------------- #

_DRIVER_CANCELLABLE = {
    "requested",
    "searching",
    "driver_assigned",
    "driver_accepted",
    "en_route",
    "driver_arrived",
}


def test_started_or_ended_states_are_not_driver_cancellable():
    for terminal in ("in_progress", "completed", "cancelled"):
        assert terminal not in _DRIVER_CANCELLABLE


# --------------------------------------------------------------------------- #
# Layer 2 — integration (imports backend; runs in CI)
# --------------------------------------------------------------------------- #


def test_driver_cancel_returns_409_when_ride_left_pretrip_state():
    """update_one (the atomic claim) matching zero rows -> 409, nothing else runs."""
    from fastapi import HTTPException

    from backend.routes import drivers as drv

    driver = {"id": "drv-1", "user_id": "user-1"}
    # Passes the in-memory check (not in_progress/completed) but the row has
    # since flipped, so the status-guarded claim matches nothing.
    ride = {"id": "ride-1", "status": "driver_accepted", "rider_id": "rider-1", "driver_id": "drv-1"}

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=None)) as upd,
        patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()) as avail,
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(drv.cancel_ride(ride_id="ride-1", reason="", request=None, current_user={"id": "user-1"}))

    assert exc_info.value.status_code == 409
    assert upd.await_count == 1  # only the status-guarded claim was attempted
    avail.assert_not_awaited()  # driver cleanup never ran on the rejected claim


def test_driver_cancel_rejects_non_owning_driver():
    """IDOR guard (findings 5/6/7): a driver cancelling a ride assigned to
    someone else gets 403 before any write, driver cleanup, or insurance-period
    transition runs."""
    from fastapi import HTTPException

    from backend.routes import drivers as drv

    attacker = {"id": "drv-attacker", "user_id": "user-attacker"}
    # Ride belongs to a different driver, in a pre-trip cancellable state.
    ride = {"id": "ride-1", "status": "driver_accepted", "rider_id": "rider-1", "driver_id": "drv-victim"}

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[attacker])),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=None)) as upd,
        patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()) as avail,
        patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()) as period,
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                drv.cancel_ride(ride_id="ride-1", reason="", request=None, current_user={"id": "user-attacker"})
            )

    assert exc_info.value.status_code == 403
    upd.assert_not_awaited()  # no cancel write ever attempted against another driver's ride
    avail.assert_not_awaited()
    period.assert_not_awaited()  # no phantom period-1 row recorded for the attacker


def test_driver_cancel_rejects_unassigned_searching_ride():
    """A `searching` ride has no assigned driver — a driver-side cancel of it is
    never legitimate and must 403 (driver_id is None != caller)."""
    from fastapi import HTTPException

    from backend.routes import drivers as drv

    driver = {"id": "drv-1", "user_id": "user-1"}
    ride = {"id": "ride-1", "status": "searching", "rider_id": "rider-1", "driver_id": None}

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=None)) as upd,
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(drv.cancel_ride(ride_id="ride-1", reason="", request=None, current_user={"id": "user-1"}))

    assert exc_info.value.status_code == 403
    upd.assert_not_awaited()


def test_driver_cancel_broadcasts_is_scheduled_for_admin_visibility():
    """Scheduled-rides gap review Finding #12: driver-cancel-after-accept is
    unconditionally terminal for every ride type (no auto-requeue) -- but a
    scheduled ride's cancellation must be tagged is_scheduled=True in both
    the ride_status_changed and ride_cancelled admin broadcasts, so ops can
    tell it apart from a routine on-demand cancellation."""
    from backend.routes import drivers as drv

    driver = {"id": "drv-1", "user_id": "user-1"}
    ride = {
        "id": "ride-1",
        "status": "driver_accepted",
        "rider_id": "rider-1",
        "driver_id": "drv-1",
        "is_scheduled": True,
    }

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value={"id": "ride-1"})),
        patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
        patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()) as bcast_status,
        patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()) as bcast_admin,
        patch("backend.routes.drivers._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        result = asyncio.run(
            drv.cancel_ride(ride_id="ride-1", reason="running late", request=None, current_user={"id": "user-1"})
        )

    assert result == {"success": True}
    bcast_status.assert_awaited_once()
    assert bcast_status.await_args.kwargs["is_scheduled"] is True
    bcast_admin.assert_awaited_once()
    admin_payload = bcast_admin.await_args.args[0]
    assert admin_payload["type"] == "ride_cancelled"
    assert admin_payload["is_scheduled"] is True


def test_driver_cancel_broadcasts_is_scheduled_false_for_on_demand_ride():
    """The same broadcasts must carry is_scheduled=False (not omitted, not
    None) for a normal on-demand ride, so the admin-dashboard field is
    always present and type-safe rather than optional-and-sometimes-missing."""
    from backend.routes import drivers as drv

    driver = {"id": "drv-1", "user_id": "user-1"}
    ride = {
        "id": "ride-1",
        "status": "driver_accepted",
        "rider_id": "rider-1",
        "driver_id": "drv-1",
        "is_scheduled": False,
    }

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value={"id": "ride-1"})),
        patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
        patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()) as bcast_status,
        patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()) as bcast_admin,
        patch("backend.routes.drivers._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        asyncio.run(drv.cancel_ride(ride_id="ride-1", reason="", request=None, current_user={"id": "user-1"}))

    assert bcast_status.await_args.kwargs["is_scheduled"] is False
    admin_payload = bcast_admin.await_args.args[0]
    assert admin_payload["is_scheduled"] is False


def test_driver_noshow_returns_409_and_charges_nothing_on_race():
    """No-show claim fails -> 409 before any fee is computed or charged."""
    from fastapi import HTTPException

    from backend.routes import drivers as drv

    driver = {"id": "drv-1", "user_id": "user-1"}
    arrived = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    ride = {
        "id": "ride-1",
        "status": "driver_arrived",
        "driver_id": "drv-1",
        "rider_id": "rider-1",
        "driver_arrived_at": arrived,
        "service_area_id": None,
    }

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={"noshow_wait_seconds": 300})),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=None)) as upd,
        patch("backend.services.cancellation_service.calculate_noshow_fee", MagicMock()) as fee_calc,
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(drv.mark_rider_noshow(ride_id="ride-1", current_user={"id": "user-1"}))

    assert exc_info.value.status_code == 409
    fee_calc.assert_not_called()  # nothing charged — claim failed before fee calc
    assert upd.await_count == 1  # only the atomic claim ran (no wallet debit)
