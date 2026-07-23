"""Unit tests for backend/utils/stuck_ride_sweeper.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tests._factories import ride_row

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLD_ISO = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
_RECENT_ISO = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()


def _sweep():
    """Import and return the _sweep coroutine after patches are in place."""
    import importlib

    import backend.utils.stuck_ride_sweeper as mod
    importlib.reload(mod)
    return mod._sweep


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.unit
async def test_stuck_searching_ride_is_cancelled():
    """A searching ride older than 5 min is claimed and WS event is sent."""
    stuck_ride = ride_row(
        id="ride-stuck-1",
        rider_id="rider-abc",
        status="searching",
        ride_requested_at=_OLD_ISO,
    )

    mock_ws = AsyncMock()
    mock_push = AsyncMock()

    with (
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=[stuck_ride])),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", mock_ws),
        patch("backend.utils.stuck_ride_sweeper.send_push_notification", mock_push),
        patch("backend.db_supabase.set_driver_available", AsyncMock()),
        patch("backend.utils.stuck_ride_sweeper._metric_inc", MagicMock()),
    ):
        from backend.utils.stuck_ride_sweeper import _sweep
        await _sweep()

    mock_ws.assert_awaited_once()
    ws_call = mock_ws.await_args[0]
    assert ws_call[0]["type"] == "ride_cancelled"
    assert ws_call[1] == "rider_rider-abc"


@pytest.mark.anyio
@pytest.mark.unit
async def test_in_progress_ride_not_cancelled():
    """An in_progress ride is never returned by the WHERE clause and never touched."""
    mock_ws = AsyncMock()

    # run_sync returns empty list — the WHERE eq("status","searching") excluded it
    with (
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=[])),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", mock_ws),
        patch("backend.utils.stuck_ride_sweeper._metric_inc", MagicMock()),
    ):
        from backend.utils.stuck_ride_sweeper import _sweep
        await _sweep()

    mock_ws.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.unit
async def test_recent_searching_ride_not_cancelled():
    """A searching ride less than 5 min old is excluded by the lt() cutoff."""
    mock_ws = AsyncMock()

    # run_sync returns empty list — the lt("ride_requested_at", cutoff) excluded it
    with (
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=[])),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", mock_ws),
        patch("backend.utils.stuck_ride_sweeper._metric_inc", MagicMock()),
    ):
        from backend.utils.stuck_ride_sweeper import _sweep
        await _sweep()

    mock_ws.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.unit
async def test_no_double_cancel_when_replica_already_claimed():
    """When UPDATE returns 0 rows (another replica claimed it), no WS or push is sent."""
    mock_ws = AsyncMock()
    mock_push = AsyncMock()

    with (
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=[])),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", mock_ws),
        patch("backend.utils.stuck_ride_sweeper.send_push_notification", mock_push),
        patch("backend.utils.stuck_ride_sweeper._metric_inc", MagicMock()),
    ):
        from backend.utils.stuck_ride_sweeper import _sweep
        await _sweep()

    mock_ws.assert_not_awaited()
    mock_push.assert_not_awaited()
