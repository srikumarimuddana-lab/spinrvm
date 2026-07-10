"""
P1-9: Multi-stop E2E (R11)

The backend supports adding and removing stops on an active ride
(POST /rides/{id}/stops, DELETE /rides/{id}/stops/{idx}).
These tests pin:
  - Stop is appended / inserted at the correct position
  - Driver receives a `stops_updated` WS event
  - Authorization guards (non-owner, wrong status)
  - Remove stop: list shrinks and driver is notified
  - Fare recalculation on mid-trip stop is NOT yet implemented — documented
    as xfail so CI flags the gap as a living TODO.

Run:
    pytest backend/tests/test_p1_multi_stop.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

RIDER_ID = "rider_p1_9"
DRIVER_USER_ID = "driver_user_p1_9"
DRIVER_ID = "driver_row_p1_9"
RIDE_ID = "ride_p1_9_001"


def _ride(status: str, stops: list | None = None, **extra) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": status,
        "driver_id": DRIVER_ID,
        "stops": stops or [],
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.12,
        "dropoff_lng": -106.65,
        "pickup_address": "123 Main",
        "dropoff_address": "456 Broadway",
        "estimated_fare": 18.5,
        "total_fare": 18.5,
        "grand_total": 18.5,
        "distance_km": 3.2,
        "duration_minutes": 8,
        "payment_method": "card",
        "payment_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def _driver_row() -> dict:
    return {"id": DRIVER_ID, "user_id": DRIVER_USER_ID, "name": "Driver P1-9"}


def _stop(address: str, lat: float = 52.14, lng: float = -106.66) -> dict:
    return {"address": address, "lat": lat, "lng": lng}


# ─────────────────────────────────────────────────────────────────────────────
# POST /{ride_id}/stops — add stop mid-trip
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestAddStopMidTrip:
    """Pins add_stop_mid_trip: stop appended, driver notified via WS.

    Code under test: backend/routes/rides.py::add_stop_mid_trip (~line 1583).
    """

    async def _call_add_stop(
        self,
        ride,
        address="456 Side St",
        lat=52.14,
        lng=-106.68,
        position=None,
    ):
        from backend.routes import rides as rides_mod

        class _Req:
            pass

        req = _Req()
        req.address = address
        req.lat = lat
        req.lng = lng
        req.position = position

        ws_calls = []

        async def _capture_ws(message, channel):
            ws_calls.append((channel, message))

        updated_ride = {**ride, "stops": ride.get("stops", []) + [_stop(address, lat, lng)]}

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride, _driver_row()])),
            patch("backend.routes.rides._deps.db.update_one", AsyncMock(return_value=updated_ride)),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock(side_effect=_capture_ws)),
        ):
            result = await rides_mod.add_stop_mid_trip(
                ride_id=RIDE_ID,
                req=req,
                current_user={"id": RIDER_ID},
            )

        return result, ws_calls

    async def test_appends_stop_to_empty_list(self):
        ride = _ride(status="in_progress")
        result, ws_calls = await self._call_add_stop(ride)

        assert result["success"] is True
        assert len(result["stops"]) == 1
        assert result["stops"][0]["address"] == "456 Side St"

    async def test_appends_stop_to_existing_list(self):
        ride = _ride(status="driver_accepted", stops=[_stop("Previous Stop")])
        result, ws_calls = await self._call_add_stop(ride, address="New Stop")

        assert len(result["stops"]) == 2
        assert result["stops"][-1]["address"] == "New Stop"

    async def test_notifies_driver_via_websocket(self):
        ride = _ride(status="in_progress")
        result, ws_calls = await self._call_add_stop(ride)

        driver_events = [(ch, msg) for ch, msg in ws_calls if f"driver_{DRIVER_USER_ID}" in str(ch)]
        assert driver_events, "Driver was not notified via WS after stop added"
        assert driver_events[0][1]["type"] == "stops_updated"
        assert driver_events[0][1]["ride_id"] == RIDE_ID

    async def test_rejects_stop_on_searching_ride(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride(status="searching")

        class _Req:
            address = "456 Side St"
            lat = 52.14
            lng = -106.68
            position = None

        with patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.add_stop_mid_trip(
                    ride_id=RIDE_ID,
                    req=_Req(),
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 400
        assert "active ride" in exc_info.value.detail.lower()

    async def test_rejects_non_rider_owner(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride(status="in_progress")

        class _Req:
            address = "456 Side St"
            lat = 52.14
            lng = -106.68
            position = None

        with patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.add_stop_mid_trip(
                    ride_id=RIDE_ID,
                    req=_Req(),
                    current_user={"id": "some-other-rider"},
                )

        assert exc_info.value.status_code == 403

    async def test_fare_is_recalculated_after_stop_added(self):
        """Adding a stop should trigger a fare recalculation."""
        ride = _ride(status="in_progress")

        class _Req:
            address = "Detour Ave"
            lat = 52.20
            lng = -106.70
            position = None

        updated_calls = []

        async def _capture_update(table, filter_doc, update_doc):
            updated_calls.append(update_doc)
            return {}

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride, _driver_row()])),
            patch("backend.routes.rides._deps.db.update_one", AsyncMock(side_effect=_capture_update)),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        ):
            from backend.routes import rides as rides_mod

            await rides_mod.add_stop_mid_trip(
                ride_id=RIDE_ID,
                req=_Req(),
                current_user={"id": RIDER_ID},
            )

        # Assert that the update included a new estimated_fare — not yet implemented
        updates = updated_calls[0].get("$set", {}) if updated_calls else {}
        assert "estimated_fare" in updates, "Fare was not recalculated — add_stop_mid_trip must update estimated_fare"


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /{ride_id}/stops/{stop_index} — remove stop mid-trip
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRemoveStopMidTrip:
    """Pins remove_stop_mid_trip: stop removed by index, driver notified.

    Code under test: backend/routes/rides.py::remove_stop_mid_trip (~line 1620).
    """

    async def test_removes_stop_at_given_index(self):
        from backend.routes import rides as rides_mod

        stops = [_stop("Stop A"), _stop("Stop B"), _stop("Stop C")]
        ride = _ride(status="in_progress", stops=stops)

        _updated_stops = [_stop("Stop A"), _stop("Stop C")]

        ws_calls = []

        async def _capture_ws(message, channel):
            ws_calls.append((channel, message))

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride, _driver_row()])),
            patch("backend.routes.rides._deps.db.update_one", AsyncMock(return_value={})),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock(side_effect=_capture_ws)),
        ):
            result = await rides_mod.remove_stop_mid_trip(
                ride_id=RIDE_ID,
                stop_index=1,  # removes Stop B
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        assert len(result["stops"]) == 2
        remaining_addresses = [s["address"] for s in result["stops"]]
        assert "Stop B" not in remaining_addresses

    async def test_notifies_driver_after_remove(self):
        from backend.routes import rides as rides_mod

        stops = [_stop("Stop A"), _stop("Stop B")]
        ride = _ride(status="in_progress", stops=stops)

        ws_calls = []

        async def _capture_ws(message, channel):
            ws_calls.append((channel, message))

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride, _driver_row()])),
            patch("backend.routes.rides._deps.db.update_one", AsyncMock(return_value={})),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock(side_effect=_capture_ws)),
        ):
            await rides_mod.remove_stop_mid_trip(
                ride_id=RIDE_ID,
                stop_index=0,
                current_user={"id": RIDER_ID},
            )

        driver_ws = [msg for ch, msg in ws_calls if f"driver_{DRIVER_USER_ID}" in str(ch)]
        assert driver_ws, "Driver not notified after stop removed"
        assert driver_ws[0]["type"] == "stops_updated"

    async def test_rejects_invalid_stop_index(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride(status="in_progress", stops=[_stop("Only Stop")])

        with patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.remove_stop_mid_trip(
                    ride_id=RIDE_ID,
                    stop_index=5,  # out of range
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 400
        assert "Invalid stop index" in exc_info.value.detail
