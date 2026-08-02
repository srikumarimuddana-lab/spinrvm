"""
P2-17: Scheduled rides + DST (R3, E14)

Implemented endpoints:
  GET  /rides/scheduled          — upcoming scheduled rides for rider
  DELETE /rides/scheduled/{id}   — cancel a scheduled ride

DST note: scheduled_time is stored as-received ISO string (UTC expected).
CreateRideRequest.validate_scheduled_time rejects local times that fall
inside a DST gap (e.g. 02:30 America/Toronto on spring-forward night) when
scheduled_timezone is provided (zoneinfo round-trip guard).

These tests pin:
  - get_scheduled_rides returns the rider's upcoming rides (get_rows filtered
    on rider_id + is_scheduled, terminal statuses excluded)
  - cancel_scheduled_ride (C1): pre-dispatch rides are cancelled via an
    atomic status-filtered claim (never an id-only write); dispatched rides
    delegate to cancel_ride_rider (full cleanup path); in_progress can never
    be cancelled here; only-owner guard; non-scheduled ride → 404;
    already-cancelled → 400
  - DST boundary: UTC-stored scheduled_time round-trips correctly (E14 happy path)
  - DST gap: booking a non-existent local time is rejected with a ValidationError

Run:
    pytest backend/tests/test_p2_scheduled_rides.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

RIDER_ID = "rider_p2_17"
RIDE_ID = "ride_p2_17_001"


def _scheduled_ride(status: str = "searching", **extra) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": status,
        "is_scheduled": True,
        "scheduled_time": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "pickup_address": "123 Main",
        "dropoff_address": "456 Broadway",
        **extra,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /rides/scheduled
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
# No explicit async marker: asyncio_mode=auto handles these. An explicit
# @pytest.mark.asyncio (or anyio) on a class routes through pytest-asyncio
# 0.23's legacy get_event_loop() wrapper, which blows up order-dependently
# once any earlier test leaves the main thread without a set event loop.
class TestGetScheduledRides:
    """Pins get_scheduled_rides: returns rider's upcoming scheduled rides.

    Code under test: backend/routes/rides.py::get_scheduled_rides (~line 1925).
    """

    async def test_returns_scheduled_rides_list(self):
        from backend.routes import rides as rides_mod

        rides = [_scheduled_ride(), _scheduled_ride(id="ride-002")]

        with patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=rides)) as get_rows:
            result = await rides_mod.get_scheduled_rides(
                current_user={"id": RIDER_ID},
            )

        assert isinstance(result, list)
        assert len(result) == 2
        table, filters = get_rows.await_args.args[:2]
        assert table == "rides"
        assert filters["rider_id"] == RIDER_ID
        assert filters["is_scheduled"] is True

    async def test_returns_empty_list_when_no_scheduled_rides(self):
        from backend.routes import rides as rides_mod

        with patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = await rides_mod.get_scheduled_rides(
                current_user={"id": RIDER_ID},
            )

        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /rides/scheduled/{ride_id}
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
class TestCancelScheduledRide:
    """Pins cancel_scheduled_ride: owner guard, status update, guards.

    Code under test: backend/routes/rides.py::cancel_scheduled_ride (~line 1933).
    """

    async def test_cancel_predispatch_uses_atomic_claim(self):
        """A still-'scheduled' ride is cancelled via an atomic status-filtered
        update_one claim (C1) — never an id-only write that could clobber a
        ride the dispatch loop has already taken live."""
        from backend.routes import rides as rides_mod

        ride = _scheduled_ride(status="scheduled")
        claim_calls = []

        async def _update_one(table, filters, update):
            claim_calls.append((table, filters, update))
            return {**ride, **update}

        with (
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[ride])),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_ride_status", AsyncMock()) as ws_broadcast,
        ):
            result = await rides_mod.cancel_scheduled_ride(
                ride_id=RIDE_ID,
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        assert claim_calls, "Ride was not updated"
        table, filters, update = claim_calls[0]
        assert table == "rides"
        # TOCTOU guard: the write must be filtered to the pre-dispatch state
        # and the owner, not an id-only update.
        assert filters.get("status") == "scheduled"
        assert filters.get("rider_id") == RIDER_ID
        assert update["status"] == "cancelled"
        # Every state change emits a WS event (CLAUDE.md invariant).
        ws_broadcast.assert_awaited_once()

    async def test_cancel_dispatched_ride_delegates_to_full_cancel_path(self):
        """Once the dispatch loop flips the ride live (is_scheduled stays
        True), cancel must run through cancel_ride_rider — atomic pre-trip
        claim, fee, driver release, insurance period, WS — never a bare
        status write (C1)."""
        from backend.routes import rides as rides_mod

        ride = _scheduled_ride(status="searching")
        delegate = AsyncMock(return_value={"success": True, "cancellation_fee": 0})

        with (
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[ride])),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock()) as update_one,
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()) as update_ride,
            patch.object(rides_mod.cancellation, "cancel_ride_rider", delegate),
        ):
            result = await rides_mod.cancel_scheduled_ride(
                ride_id=RIDE_ID,
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        delegate.assert_awaited_once()
        assert delegate.await_args.args[0] == RIDE_ID
        # The scheduled-cancel endpoint itself must not write anything for a
        # dispatched ride — all writes belong to the delegated path.
        update_one.assert_not_awaited()
        update_ride.assert_not_awaited()

    async def test_cancel_predispatch_claim_race_falls_through_to_live_path(self):
        """If the dispatch loop flips scheduled → searching between the read
        and the claim, the zero-row claim must NOT report success on a stale
        state — it re-reads and delegates to the full cancel path (C1)."""
        from backend.routes import rides as rides_mod

        ride = _scheduled_ride(status="scheduled")
        live_ride = _scheduled_ride(status="searching")
        delegate = AsyncMock(return_value={"success": True, "cancellation_fee": 0})

        with (
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[ride])),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=live_ride)),
            patch.object(rides_mod.cancellation, "cancel_ride_rider", delegate),
        ):
            result = await rides_mod.cancel_scheduled_ride(
                ride_id=RIDE_ID,
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        delegate.assert_awaited_once()

    async def test_cancel_in_progress_scheduled_ride_is_rejected(self):
        """An in_progress ride that started life as scheduled can NEVER be
        cancelled through this endpoint, and nothing is written (C1 — the old
        id-only write cancelled live trips underneath the driver)."""
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.utils.error_handling import SpinrException

        ride = _scheduled_ride(status="in_progress")

        async def _find_one(table, filters=None, **kwargs):
            # _require_ride_in_state_rider: the status-filtered lookup misses
            # (in_progress is not cancellable), the ownership lookup hits → 409.
            if filters and "status" in filters:
                return None
            return ride

        with (
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[ride])),
            patch("backend.routes.rides._deps.db_supabase.find_one", AsyncMock(side_effect=_find_one)),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock()) as update_one,
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()) as update_ride,
        ):
            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await rides_mod.cancel_scheduled_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 409
        update_one.assert_not_awaited()
        update_ride.assert_not_awaited()

    async def test_non_owner_or_non_scheduled_returns_404(self):
        """Ride lookup includes rider_id + is_scheduled filter;
        wrong owner or non-scheduled ride returns 404."""
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.utils.error_handling import SpinrException

        with patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await rides_mod.cancel_scheduled_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": "different-rider"},
                )

        assert exc_info.value.status_code == 404

    async def test_already_cancelled_raises_400(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.utils.error_handling import SpinrException

        ride = _scheduled_ride(status="cancelled")

        with patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[ride])):
            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await rides_mod.cancel_scheduled_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 400
        # SpinrException uses .message; HTTPException uses .detail
        text = getattr(exc_info.value, "detail", None) or getattr(exc_info.value, "message", "")
        assert "cancel" in str(text).lower()

    async def test_completed_ride_cannot_be_cancelled(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.utils.error_handling import SpinrException

        ride = _scheduled_ride(status="completed")

        with patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[ride])):
            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await rides_mod.cancel_scheduled_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# DST boundary (E14)
# ─────────────────────────────────────────────────────────────────────────────


def _frozen_datetime(fixed_utc: datetime):
    """datetime subclass whose .now() always returns ``fixed_utc``.

    The DST-boundary tests below use fixed calendar dates (real spring-forward
    Sundays) as fixtures, which is the right way to test DST-gap detection —
    but Finding #02's max-advance-window check (also in validate_scheduled_time,
    also relative to datetime.now()) means those fixed dates only pass if
    "now" is patched to sit within the window of the fixture date, regardless
    of when the test suite actually runs.
    """

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    return _Frozen


def _patch_schemas_now(fixed_utc: datetime):
    """Freeze datetime.now() as seen by CreateRideRequest.validate_scheduled_time.

    backend/schemas/__init__.py shadows the flat backend/schemas.py module: it
    exec's the flat file under a private synthetic module name and copies its
    public names into the package namespace. That means `backend.schemas.datetime`
    (the package attribute) and the `datetime` name the validator function
    actually resolves at call time (its own __globals__, i.e. the synthetic
    module's dict) are two different bindings — patching the former is a
    no-op for validator behavior. Patch the validator's own __globals__
    instead, which is correct regardless of that indirection.
    """
    from backend.schemas import CreateRideRequest

    target_globals = CreateRideRequest.validate_scheduled_time.__func__.__globals__
    return patch.dict(target_globals, {"datetime": _frozen_datetime(fixed_utc)})


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
        # Freeze "now" a few days ahead of the fixture date so it lands
        # inside the max-advance window regardless of actual run date.
        frozen_now = datetime(2027, 3, 10, tzinfo=timezone.utc)
        with (
            _patch_schemas_now(frozen_now),
            pytest.raises(ValidationError) as exc_info,
        ):
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
        frozen_now = datetime(2027, 3, 10, tzinfo=timezone.utc)
        with _patch_schemas_now(frozen_now):
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

    def test_dst_fall_back_ambiguous_hour_is_rejected(self):
        """Finding #10: the November fall-back repeated hour was previously
        unguarded — only the spring-forward gap was checked. 2026-11-01 is
        the fall-back Sunday for Eastern time (clocks go 2:00 EDT -> 1:00
        EST), so 01:30 occurs twice and is ambiguous."""
        from pydantic import ValidationError

        from backend.schemas import CreateRideRequest

        frozen_now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        with _patch_schemas_now(frozen_now), pytest.raises(ValidationError) as exc_info:
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
                scheduled_time=datetime(2026, 11, 1, 1, 30),
            )

        errors_text = str(exc_info.value).lower()
        assert "ambiguous" in errors_text or "fall-back" in errors_text or "twice" in errors_text

    def test_time_outside_fall_back_hour_is_unaffected(self):
        """A normal time on the same fall-back date, outside the repeated
        hour, must not be rejected by the new ambiguity guard."""
        from backend.schemas import CreateRideRequest

        frozen_now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        with _patch_schemas_now(frozen_now):
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
                scheduled_time=datetime(2026, 11, 1, 10, 0),
            )
        assert ride_req.scheduled_time is not None

    def test_utc_scheduled_time_unaffected_by_fall_back_guard(self):
        """No scheduled_timezone means the fall-back guard (which only
        triggers under the tz_name branch) never runs at all -- a UTC
        timestamp during the real-world fall-back window must pass
        untouched, exactly like the pre-existing UTC round-trip test."""
        from backend.schemas import CreateRideRequest

        frozen_now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        with _patch_schemas_now(frozen_now):
            ride_req = CreateRideRequest(
                vehicle_type_id="standard",
                pickup_address="123 Main St",
                pickup_lat=52.1,
                pickup_lng=-106.0,
                dropoff_address="456 Broadway",
                dropoff_lat=52.2,
                dropoff_lng=-106.1,
                is_scheduled=True,
                scheduled_time=datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
            )
        assert ride_req.scheduled_time is not None


# ─────────────────────────────────────────────────────────────────────────────
# Max advance-booking window (Finding #02, scheduled-rides gap review)
# ─────────────────────────────────────────────────────────────────────────────


class TestMaxAdvanceWindow:
    """Server-side ceiling matching the rider app's 7-day date-picker maxDate.

    Previously enforced client-only — any other caller (direct API request,
    AI booking assistant) could schedule arbitrarily far ahead with nothing
    server-side to reject it.
    """

    def _make(self, scheduled_time):
        from backend.schemas import CreateRideRequest

        return CreateRideRequest(
            vehicle_type_id="standard",
            pickup_address="123 Main St",
            pickup_lat=52.1,
            pickup_lng=-106.0,
            dropoff_address="456 Broadway",
            dropoff_lat=52.2,
            dropoff_lng=-106.1,
            is_scheduled=True,
            scheduled_time=scheduled_time,
        )

    def test_within_seven_days_accepted(self):
        ride_req = self._make(datetime.now(timezone.utc) + timedelta(days=6))
        assert ride_req.scheduled_time is not None

    def test_beyond_seven_days_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            self._make(datetime.now(timezone.utc) + timedelta(days=8))
        assert "7 days" in str(exc_info.value)

    def test_far_future_rejected(self):
        """The gap this closes: nothing previously stopped a multi-month or
        multi-year booking from an API caller that skips the mobile client's
        date picker."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._make(datetime.now(timezone.utc) + timedelta(days=400))
