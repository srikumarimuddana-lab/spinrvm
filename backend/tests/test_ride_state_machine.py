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
        Return a context manager that patches backend.routes.drivers.db
        so find_one returns results in the order given.
        """
        mock_db = MagicMock()
        mock_db.rides = MagicMock()
        mock_db.rides.find_one = AsyncMock(side_effect=find_results)
        return mock_db

    async def test_returns_ride_when_in_allowed_state(self):
        ride = {"id": "r1", "driver_id": "d1", "status": "in_progress"}
        mock_db = await self._patched_db([ride])

        with patch("backend.routes.drivers.db", mock_db):
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

        with patch("backend.routes.drivers.db", mock_db):
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

        with patch("backend.routes.drivers.db", mock_db):
            from backend.routes.drivers import (
                COMPLETE_FROM_STATES,
                _require_ride_in_state,
            )

            with pytest.raises(HTTPException) as exc_info:
                await _require_ride_in_state("r1", "d1", COMPLETE_FROM_STATES)

            assert exc_info.value.status_code == 409

    async def test_raises_404_when_ride_does_not_exist(self):
        mock_db = await self._patched_db([None, None])

        with patch("backend.routes.drivers.db", mock_db):
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

        with patch("backend.routes.drivers.db", mock_db):
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

        with patch("backend.routes.drivers.db", mock_db):
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

        with patch("backend.routes.drivers.db", mock_db):
            from backend.routes.drivers import (
                START_FROM_STATES,
                _require_ride_in_state,
            )

            with pytest.raises(HTTPException) as exc_info:
                await _require_ride_in_state("r1", "d1", START_FROM_STATES)

            assert exc_info.value.status_code == 409


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
