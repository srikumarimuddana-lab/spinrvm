"""P1: a former rider must not be able to track a driver's live GPS via a
completed ride.

GET /rides/{id} embedded the assigned driver's live lat/lng with no ride-status
gate, so a rider could poll a terminal (completed/cancelled) ride indefinitely
and follow the driver around — a stalking vector and a PIPEDA
purpose-limitation breach. Live coordinates are now omitted on terminal rides.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_DRIVER = {
    "id": "drv_1",
    "user_id": "drvuser_1",
    "name": "Alex",
    "rating": 4.9,
    "lat": 52.13,
    "lng": -106.67,
    "vehicle_make": "Toyota",
    "license_plate": "ABC123",
}


def _ride(status: str):
    return {
        "id": "ride_1",
        "rider_id": "rider_1",
        "driver_id": "drv_1",
        "status": status,
        "pickup_lat": 52.1,
        "pickup_lng": -106.6,
    }


async def _call(status: str):
    from backend.routes.rides import queries

    current_user = {"id": "rider_1"}
    with (
        patch("backend.routes.rides.queries._deps.db_supabase.get_ride", AsyncMock(return_value=_ride(status))),
        patch("backend.routes.rides.queries._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=dict(_DRIVER))),
        patch("backend.routes.rides.queries._deps.db_supabase.get_user_by_id", AsyncMock(return_value={"id": "drvuser_1"})),
        patch("backend.routes.rides.queries._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
        patch("backend.routes.rides.queries._rider_visible_photo", lambda u: None),
        patch("backend.routes.rides.queries.get_app_settings", AsyncMock(return_value={}), create=True),
    ):
        return await queries.get_ride(ride_id="ride_1", current_user=current_user)


@pytest.mark.anyio
class TestDriverGpsTerminalRedaction:
    async def test_active_ride_exposes_live_location(self):
        result = await _call("driver_accepted")
        driver = result["driver"]
        assert driver["lat"] == 52.13
        assert driver["lng"] == -106.67

    async def test_completed_ride_redacts_location(self):
        result = await _call("completed")
        driver = result["driver"]
        assert driver["lat"] is None
        assert driver["lng"] is None
        # Non-location identity fields still present.
        assert driver["license_plate"] == "ABC123"

    async def test_cancelled_ride_redacts_location(self):
        result = await _call("cancelled")
        driver = result["driver"]
        assert driver["lat"] is None
        assert driver["lng"] is None
