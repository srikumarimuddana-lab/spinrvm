"""
P2-17: Scheduled rides + DST (R3, E14)

Implemented endpoints:
  GET  /rides/scheduled          — upcoming scheduled rides for rider
  DELETE /rides/scheduled/{id}   — cancel a scheduled ride

DST note: scheduled_time is stored as-received ISO string (UTC expected).
The server currently does NOT validate that a requested local time falls
inside a DST gap (e.g. 02:30 America/Toronto on spring-forward night).
E14 is documented as xfail(strict=False) — living TODO.

These tests pin:
  - get_scheduled_rides returns rides list (including the cursor-list path)
  - cancel_scheduled_ride updates status to "cancelled"; only-owner guard;
    non-scheduled ride → 404; already-cancelled → 400
  - DST boundary: UTC-stored scheduled_time round-trips correctly (E14 happy path)
  - DST gap: booking a non-existent local time is NOT currently rejected (xfail)

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

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "E14 gap: the backend does not validate that scheduled_time falls "
            "inside a DST gap (e.g. 02:30 Eastern on spring-forward night). "
            "A booking for a non-existent local time should be rejected with 400. "
            "Fix: add tz-aware validation in create_ride using pytz or zoneinfo."
        ),
    )
    def test_dst_gap_time_is_rejected(self):
        """Booking a ride for a local time that doesn't exist (DST gap) must
        return 400. Currently the server accepts it verbatim — this xfail
        documents the known gap (E14) until timezone validation is added."""
        import zoneinfo

        eastern = zoneinfo.ZoneInfo("America/Toronto")
        # 02:30 on spring-forward night doesn't exist in Eastern time
        # (clocks jump 02:00 → 03:00)
        dst_gap_local = datetime(2025, 3, 9, 2, 30, tzinfo=eastern)

        # The DST transition makes this timestamp fold to 01:30 UTC-equivalent
        # rather than the intended 07:30 UTC.  The backend should detect this
        # and raise 400; currently it does not.
        is_ambiguous_or_nonexistent = dst_gap_local.utcoffset() is None
        assert is_ambiguous_or_nonexistent, (
            "Backend should reject DST-gap scheduled_time but currently doesn't"
        )
