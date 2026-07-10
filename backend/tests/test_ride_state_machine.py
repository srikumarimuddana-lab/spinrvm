"""
Tests for the ride state-machine guard in routes/drivers.py.

These pin the C-RIDE-01/C-RIDE-02 fixes: the `_require_ride_in_state`
helper must reject transitions from terminal (cancelled/completed) or
wrong-source states with 409 Conflict, and return 404 only when the
ride genuinely doesn't exist.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.asyncio
class TestRequireRideInState:
    """Pin the state-machine guard behavior."""

    async def _patched_db(self, find_results):
        """
        Return a context manager that patches backend.routes.drivers._deps.db
        so find_one returns results in the order given.

        The production code calls the flat Supabase API:
            db.find_one("rides", {...})
        not the MongoDB-style collection attribute approach.
        """
        mock_db = MagicMock()
        mock_db.find_one = AsyncMock(side_effect=find_results)
        return mock_db

    async def test_returns_ride_when_in_allowed_state(self):
        ride = {"id": "r1", "driver_id": "d1", "status": "in_progress"}
        mock_db = await self._patched_db([ride])

        with patch("backend.routes.drivers._deps.db", mock_db):
            from backend.routes.drivers import (
                COMPLETE_FROM_STATES,
                _require_ride_in_state,
            )

            result = await _require_ride_in_state("r1", "d1", COMPLETE_FROM_STATES)
            assert result == ride

    async def test_raises_409_when_ride_in_wrong_state(self):
        """A cancelled ride cannot be completed — must raise 409, not 404."""
        mock_db = await self._patched_db(
            [
                None,  # not found with status filter
                {"id": "r1", "driver_id": "d1", "status": "cancelled"},  # found without filter
            ]
        )

        with patch("backend.routes.drivers._deps.db", mock_db):
            from backend.routes.drivers import (
                COMPLETE_FROM_STATES,
                _require_ride_in_state,
            )

            with pytest.raises(HTTPException) as exc_info:
                await _require_ride_in_state("r1", "d1", COMPLETE_FROM_STATES)

            assert exc_info.value.status_code == 409
            assert "cancelled" in exc_info.value.detail

    async def test_raises_409_when_completing_already_completed(self):
        """Idempotent re-completion must still return 409 so the client
        knows the second call did not do work (we only include in_progress
        as the allowed source for completion)."""
        mock_db = await self._patched_db(
            [
                None,
                {"id": "r1", "driver_id": "d1", "status": "completed"},
            ]
        )

        with patch("backend.routes.drivers._deps.db", mock_db):
            from backend.routes.drivers import (
                COMPLETE_FROM_STATES,
                _require_ride_in_state,
            )

            with pytest.raises(HTTPException) as exc_info:
                await _require_ride_in_state("r1", "d1", COMPLETE_FROM_STATES)

            assert exc_info.value.status_code == 409

    async def test_raises_404_when_ride_does_not_exist(self):
        mock_db = await self._patched_db([None, None])

        with patch("backend.routes.drivers._deps.db", mock_db):
            from backend.routes.drivers import (
                COMPLETE_FROM_STATES,
                _require_ride_in_state,
            )

            with pytest.raises(HTTPException) as exc_info:
                await _require_ride_in_state("r1", "d1", COMPLETE_FROM_STATES)

            assert exc_info.value.status_code == 404

    async def test_arrive_allows_driver_assigned(self):
        ride = {"id": "r1", "driver_id": "d1", "status": "driver_assigned"}
        mock_db = await self._patched_db([ride])

        with patch("backend.routes.drivers._deps.db", mock_db):
            from backend.routes.drivers import (
                ARRIVE_FROM_STATES,
                _require_ride_in_state,
            )

            result = await _require_ride_in_state("r1", "d1", ARRIVE_FROM_STATES)
            assert result == ride

    async def test_arrive_is_idempotent_from_driver_arrived(self):
        """Retrying arrive after the first call should succeed, not 409."""
        ride = {"id": "r1", "driver_id": "d1", "status": "driver_arrived"}
        mock_db = await self._patched_db([ride])

        with patch("backend.routes.drivers._deps.db", mock_db):
            from backend.routes.drivers import (
                ARRIVE_FROM_STATES,
                _require_ride_in_state,
            )

            result = await _require_ride_in_state("r1", "d1", ARRIVE_FROM_STATES)
            assert result == ride

    async def test_start_rejects_completed_ride(self):
        """Cannot start a ride that was already completed."""
        mock_db = await self._patched_db(
            [
                None,
                {"id": "r1", "driver_id": "d1", "status": "completed"},
            ]
        )

        with patch("backend.routes.drivers._deps.db", mock_db):
            from backend.routes.drivers import (
                START_FROM_STATES,
                _require_ride_in_state,
            )

            with pytest.raises(HTTPException) as exc_info:
                await _require_ride_in_state("r1", "d1", START_FROM_STATES)

            assert exc_info.value.status_code == 409


@pytest.mark.asyncio
class TestCancelStateGuardRider:
    """Pin the rider-side cancel state guard (_require_ride_in_state_rider).

    The cancel endpoint only allows pre-trip states; in_progress and
    completed must be rejected with 409.
    """

    # States accepted by cancel_ride_rider (mirrors the inline tuple in rides.py).
    CANCEL_ALLOWED = ("requested", "searching", "driver_assigned", "en_route", "driver_arrived")

    async def _patched_rides_db(self, find_results):
        mock_db = MagicMock()
        mock_db.find_one = AsyncMock(side_effect=find_results)
        return mock_db

    async def test_cancel_rejects_in_progress_ride(self):
        """Critical invariant: in_progress → cancelled MUST be rejected with 409."""
        mock_db = await self._patched_rides_db(
            [
                None,  # not found with status filter (in_progress not in CANCEL_ALLOWED)
                {"id": "r1", "rider_id": "u1", "status": "in_progress"},  # found without filter
            ]
        )

        with patch("backend.routes.rides._deps.db", mock_db):
            from backend.routes.rides import _require_ride_in_state_rider
            from backend.utils.error_handling import SpinrException

            with pytest.raises(SpinrException) as exc_info:
                await _require_ride_in_state_rider("r1", "u1", self.CANCEL_ALLOWED)

            assert exc_info.value.status_code == 409
            assert "in_progress" in exc_info.value.message

    async def test_complete_allowed_from_in_progress(self):
        """Completing an in_progress ride must pass the state guard."""
        ride = {"id": "r1", "driver_id": "d1", "status": "in_progress"}
        mock_db = MagicMock()
        mock_db.find_one = AsyncMock(side_effect=[ride])

        with patch("backend.routes.drivers._deps.db", mock_db):
            from backend.routes.drivers import (
                COMPLETE_FROM_STATES,
                _require_ride_in_state,
            )

            result = await _require_ride_in_state("r1", "d1", COMPLETE_FROM_STATES)
            assert result == ride

    async def test_cancel_allowed_from_searching(self):
        """Cancel must succeed when ride is in searching state."""
        ride = {"id": "r1", "rider_id": "u1", "status": "searching"}
        mock_db = await self._patched_rides_db([ride])

        with patch("backend.routes.rides._deps.db", mock_db):
            from backend.routes.rides import _require_ride_in_state_rider

            result = await _require_ride_in_state_rider("r1", "u1", self.CANCEL_ALLOWED)
            assert result == ride

    async def test_cancel_allowed_from_driver_assigned(self):
        """Cancel must succeed when a driver has been assigned but trip not started."""
        ride = {"id": "r1", "rider_id": "u1", "status": "driver_assigned"}
        mock_db = await self._patched_rides_db([ride])

        with patch("backend.routes.rides._deps.db", mock_db):
            from backend.routes.rides import _require_ride_in_state_rider

            result = await _require_ride_in_state_rider("r1", "u1", self.CANCEL_ALLOWED)
            assert result == ride

    async def test_cancel_rejects_completed_ride(self):
        """Cancel must be rejected with 409 when ride is already completed."""
        mock_db = await self._patched_rides_db(
            [
                None,  # not found with status filter (completed not in CANCEL_ALLOWED)
                {"id": "r1", "rider_id": "u1", "status": "completed"},  # found without filter
            ]
        )

        with patch("backend.routes.rides._deps.db", mock_db):
            from backend.routes.rides import _require_ride_in_state_rider
            from backend.utils.error_handling import SpinrException

            with pytest.raises(SpinrException) as exc_info:
                await _require_ride_in_state_rider("r1", "u1", self.CANCEL_ALLOWED)

            assert exc_info.value.status_code == 409
            assert "completed" in exc_info.value.message


def test_state_constants_are_disjoint_from_terminal():
    """Sanity check: no source-state allowlist should include terminals."""
    from backend.routes.drivers import (
        ARRIVE_FROM_STATES,
        COMPLETE_FROM_STATES,
        START_FROM_STATES,
    )

    terminals = {"completed", "cancelled"}
    assert terminals.isdisjoint(set(ARRIVE_FROM_STATES))
    assert terminals.isdisjoint(set(START_FROM_STATES))
    assert terminals.isdisjoint(set(COMPLETE_FROM_STATES))


@pytest.mark.asyncio
class TestCompleteRideAtomicGuard:
    """Completion must be a CAS on status='in_progress'.

    The read-then-write window in complete_ride means two concurrent
    completions (or a complete racing a cancel) could both pass the status
    pre-check. The update filters on status='in_progress'; when it matches
    zero rows the loser must raise RideStateError without running any
    post-completion side-effects (driver availability, period transition,
    completion broadcast).
    """

    async def test_concurrent_completion_loses_cas_raises_ride_state_error(self):
        from backend.routes import drivers as drv
        from backend.utils.error_handling import RideStateError

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
            "total_fare": 8.0,
        }
        driver = {"id": "d1", "user_id": "u1"}
        captured_filters: dict = {}

        async def fake_update_one(table, filters, updates, **kw):
            if table == "rides" and updates.get("status") == "completed":
                captured_filters.update(filters)
                return None  # CAS matched zero rows — a concurrent request won
            return {"id": "r1"}

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [driver]
            if table == "rides":
                return [ride]
            return []  # driver_location_history → no GPS recalculation

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=fake_update_one)),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()) as set_available,
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()) as period_transition,
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()) as ws_send,
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            with pytest.raises(RideStateError):
                await drv.complete_ride(ride_id="r1", current_user={"id": "u1"})

        # The completion update must carry the CAS status filter.
        assert captured_filters.get("status") == "in_progress"
        # Losing the race must not trigger post-completion side-effects.
        set_available.assert_not_awaited()
        period_transition.assert_not_awaited()
        ws_send.assert_not_awaited()
