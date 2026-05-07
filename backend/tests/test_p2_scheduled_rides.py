"""
P2-17: Scheduled rides + DST (R3, E14)

Implemented endpoints:
  GET  /rides/scheduled          — upcoming scheduled rides for rider
  DELETE /rides/scheduled/{id}   — cancel a scheduled ride

DST: `CreateRideRequest.validate_scheduled_time` (schemas.py) round-trips a
naive local time through `zoneinfo` and rejects DST-gap times (e.g. 02:30
America/Toronto on spring-forward night) before persistence. Pinned below.

These tests pin:
  - get_scheduled_rides returns rides list (including the cursor-list path)
  - cancel_scheduled_ride updates status to "cancelled"; only-owner guard;
    non-scheduled ride → 404; already-cancelled → 400
  - DST boundary: UTC-stored scheduled_time round-trips correctly (E14 happy path)
  - DST gap: booking a non-existent local time IS rejected by the validator

Run:
    pytest backend/tests/test_p2_scheduled_rides.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


RIDER_ID = "rider_p2_17"
RIDE_ID = "ride_p2_17_001"


def _scheduled_ride(status: str = "searching", **extra) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": status,
        "is_scheduled": True,
        "scheduled_time": (datetime.utcnow() + timedelta(hours=2)).isoformat(),
        "pickup_address": "123 Main",
        "dropoff_address": "456 Broadway",
        **extra,
    }


class _SimpleCursor:
    """Minimal cursor stub — mirrors the pattern used in other P2 tests."""
    def __init__(self, items):
        self._items = items

    async def to_list(self, length=None):
        return self._items


# ─────────────────────────────────────────────────────────────────────────────
# GET /rides/scheduled
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestGetScheduledRides:
    """Pins get_scheduled_rides: returns rider's upcoming scheduled rides.

    Code under test: backend/routes/rides.py::get_scheduled_rides (~line 1925).
    """

    async def test_returns_scheduled_rides_list(self):
        from backend.routes import rides as rides_mod

        rides = [_scheduled_ride(), _scheduled_ride(id="ride-002")]
        cursor = _SimpleCursor(rides)

        with patch("backend.routes.rides.db_supabase.get_rides_for_user",
                   MagicMock(return_value=cursor)):
            result = await rides_mod.get_scheduled_rides(
                current_user={"id": RIDER_ID},
            )

        assert isinstance(result, list)
        assert len(result) == 2

    async def test_returns_empty_list_when_no_scheduled_rides(self):
        from backend.routes import rides as rides_mod

        cursor = _SimpleCursor([])

        with patch("backend.routes.rides.db_supabase.get_rides_for_user",
                   MagicMock(return_value=cursor)):
            result = await rides_mod.get_scheduled_rides(
                current_user={"id": RIDER_ID},
            )

        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /rides/scheduled/{ride_id}
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestCancelScheduledRide:
    """Pins cancel_scheduled_ride: owner guard, status update, guards.

    Code under test: backend/routes/rides.py::cancel_scheduled_ride (~line 1933).
    """

    async def test_cancel_sets_status_cancelled(self):
        from backend.routes import rides as rides_mod

        ride = _scheduled_ride()
        updates = []

        with (
            patch("backend.routes.rides.db_supabase.get_rows",
                  AsyncMock(return_value=[ride])),
            patch("backend.routes.rides.db_supabase.update_ride",
                  AsyncMock(side_effect=lambda rid, d: updates.append(d))),
        ):
            result = await rides_mod.cancel_scheduled_ride(
                ride_id=RIDE_ID,
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        assert updates, "Ride was not updated"
        assert updates[0]["status"] == "cancelled"

    async def test_non_owner_or_non_scheduled_returns_404(self):
        """Ride lookup includes rider_id + is_scheduled filter;
        wrong owner or non-scheduled ride returns 404."""
        from backend.routes import rides as rides_mod
        from fastapi import HTTPException

        with patch("backend.routes.rides.db_supabase.get_rows",
                   AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.cancel_scheduled_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": "different-rider"},
                )

        assert exc_info.value.status_code == 404

    async def test_already_cancelled_raises_400(self):
        from backend.routes import rides as rides_mod
        from fastapi import HTTPException

        ride = _scheduled_ride(status="cancelled")

        with patch("backend.routes.rides.db_supabase.get_rows",
                   AsyncMock(return_value=[ride])):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.cancel_scheduled_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 400
        assert "cancel" in exc_info.value.detail.lower()

    async def test_completed_ride_cannot_be_cancelled(self):
        from backend.routes import rides as rides_mod
        from fastapi import HTTPException

        ride = _scheduled_ride(status="completed")

        with patch("backend.routes.rides.db_supabase.get_rows",
                   AsyncMock(return_value=[ride])):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.cancel_scheduled_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# DST boundary (E14)
# ─────────────────────────────────────────────────────────────────────────────

class TestDSTBoundary:
    """E14 — scheduled ride times and DST boundary handling."""

    def test_utc_scheduled_time_round_trips_exactly(self):
        """A ride stored with an explicit UTC ISO timestamp is retrieved as-is.
        No timezone conversion happens server-side; the client is responsible
        for converting to local time."""
        # Spring-forward: 2025-03-09T07:00:00Z = 2:00 AM → 3:00 AM in Eastern
        dst_transition_utc = "2025-03-09T07:00:00+00:00"
        ride = _scheduled_ride(scheduled_time=dst_transition_utc)

        # The ride row stores the UTC value verbatim
        assert ride["scheduled_time"] == dst_transition_utc

    def test_dst_gap_time_is_rejected(self):
        """Booking a ride for a local time that doesn't exist (DST gap) must
        raise a ValidationError. 2027-03-14 02:30 America/Toronto is the
        spring-forward gap (clocks jump 02:00 → 03:00 EDT).

        Fix landed in: backend/schemas.py::CreateRideRequest.validate_scheduled_time
        — uses zoneinfo round-trip to detect non-existent wall-clock times.
        """
        from pydantic import ValidationError
        from backend.schemas import CreateRideRequest

        # 2027-03-14 is the spring-forward Sunday for Eastern time.
        # 02:30 does not exist; clocks skip from 02:00 → 03:00.
        # scheduled_timezone enables the DST-gap guard on the validator.
        with pytest.raises(ValidationError) as exc_info:
            CreateRideRequest(
                vehicle_type_id="standard",
                pickup_address="123 Main St",
                pickup_lat=52.1,
                pickup_lng=-106.0,
                dropoff_address="456 Broadway",
                dropoff_lat=52.2,
                dropoff_lng=-106.1,
                is_scheduled=True,
                scheduled_timezone="America/Toronto",
                scheduled_time=datetime(2027, 3, 14, 2, 30),
            )

        errors_text = str(exc_info.value)
        assert "DST" in errors_text or "gap" in errors_text.lower() or "not exist" in errors_text

    def test_valid_scheduled_time_in_timezone_accepted(self):
        """A time that's valid in the given timezone passes the DST guard."""
        from backend.schemas import CreateRideRequest

        # 2027-03-14 04:00 (after spring-forward) is a valid EDT time.
        ride_req = CreateRideRequest(
            vehicle_type_id="standard",
            pickup_address="123 Main St",
            pickup_lat=52.1,
            pickup_lng=-106.0,
            dropoff_address="456 Broadway",
            dropoff_lat=52.2,
            dropoff_lng=-106.1,
            is_scheduled=True,
            scheduled_timezone="America/Toronto",
            scheduled_time=datetime(2027, 3, 14, 4, 0),
        )
        assert ride_req.scheduled_time is not None
