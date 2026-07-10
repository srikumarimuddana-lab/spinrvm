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
            asyncio.run(
                drv.cancel_ride(ride_id="ride-1", reason="", request=None, current_user={"id": "user-1"})
            )

    assert exc_info.value.status_code == 409
    assert upd.await_count == 1  # only the status-guarded claim was attempted
    avail.assert_not_awaited()  # driver cleanup never ran on the rejected claim


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
