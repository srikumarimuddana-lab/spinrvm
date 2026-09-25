"""The "no drivers found" auto-cancel tells the rider app WHY, in a field it can key on.

The rider app shows a "No drivers available right now" sheet (Try again /
Schedule for later) instead of bouncing home, but only when the cancel
carries ``cancellation_type == "no_drivers_found"``. Both senders of this
cancel must include it, on the WebSocket message and on the push data:

- ``routes/rides/matching.py::ride_search_timeout`` (in-process timer)
- ``utils/stuck_ride_sweeper.py::_sweep`` (restart-safe backstop, same payload by design)

Purely additive: existing keys (``type``, ``ride_id``, ``reason``, ``is_auto``)
are asserted unchanged so older app builds keep working.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tests._factories import ride_row

pytestmark = pytest.mark.anyio

RIDER_ID = "rider_nodrivers"
RIDE_ID = "ride_nodrivers_001"


def _searching_ride() -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": "searching",
        "driver_id": None,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.12,
        "dropoff_lng": -106.65,
        "pickup_address": "123 Main",
        "dropoff_address": "456 Broadway",
        "payment_method": "card",
        "payment_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.unit
async def test_search_timeout_ws_and_push_carry_cancellation_type():
    from backend.routes import rides as rides_mod

    searching = _searching_ride()

    async def _claim(table, filters, patch_, retry_policy="read"):
        return {**searching, **patch_}

    ws_calls: list[tuple[str, dict]] = []

    async def _ws(message, channel):
        ws_calls.append((channel, message))

    push_mock = AsyncMock()

    with (
        patch("backend.routes.rides._deps.asyncio.sleep", AsyncMock()),
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=searching)),
        patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(side_effect=_claim)),
        patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock(side_effect=_ws)),
        patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
        patch("backend.routes.rides._deps.send_push_notification", push_mock),
        patch("backend.utils.card_hold_release.release_open_hold", AsyncMock()),
    ):
        await rides_mod.ride_search_timeout(RIDE_ID, timeout_seconds=1)

    rider_msgs = [m for c, m in ws_calls if c == f"rider_{RIDER_ID}"]

    cancelled = [m for m in rider_msgs if m.get("type") == "ride_cancelled"]
    assert len(cancelled) == 1, rider_msgs
    assert cancelled[0]["cancellation_type"] == "no_drivers_found"
    assert cancelled[0]["ride_id"] == RIDE_ID
    # Existing display text is unchanged (older app builds show it).
    assert cancelled[0]["reason"].startswith("No nearby drivers available")

    status_changed = [m for m in rider_msgs if m.get("type") == "ride_status_changed"]
    assert len(status_changed) == 1, rider_msgs
    assert status_changed[0]["status"] == "cancelled"
    assert status_changed[0]["reason"] == "no_drivers_found"
    assert status_changed[0]["cancellation_type"] == "no_drivers_found"

    push_mock.assert_awaited_once()
    data = push_mock.await_args.args[3]
    assert data == {
        "type": "ride_cancelled",
        "ride_id": RIDE_ID,
        "is_auto": "true",
        "cancellation_type": "no_drivers_found",
    }
    # FCM data values must be strings.
    assert all(isinstance(v, str) for v in data.values())


@pytest.mark.unit
async def test_search_timeout_noop_sends_nothing_when_driver_matched():
    """The new field must not leak onto a ride the timer did not cancel."""
    from backend.routes import rides as rides_mod

    matched = {**_searching_ride(), "status": "driver_accepted", "driver_id": "drv_1"}
    ws_mock = AsyncMock()
    push_mock = AsyncMock()

    with (
        patch("backend.routes.rides._deps.asyncio.sleep", AsyncMock()),
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=matched)),
        patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock()),
        patch("backend.routes.rides._deps.manager.send_personal_message", ws_mock),
        patch("backend.routes.rides._deps.send_push_notification", push_mock),
    ):
        await rides_mod.ride_search_timeout(RIDE_ID, timeout_seconds=1)

    ws_mock.assert_not_called()
    push_mock.assert_not_called()


@pytest.mark.unit
async def test_sweeper_ws_and_push_carry_cancellation_type():
    old_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    stuck = ride_row(id="ride-stuck-nd", rider_id="rider-nd", status="searching", ride_requested_at=old_iso)

    ws_mock = AsyncMock()
    push_mock = AsyncMock()

    with (
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=[stuck])),
        patch("backend.utils.stuck_ride_sweeper.release_open_hold", AsyncMock()),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", ws_mock),
        patch("backend.utils.stuck_ride_sweeper.send_push_notification", push_mock),
        patch("backend.db_supabase.set_driver_available", AsyncMock()),
        patch("backend.utils.stuck_ride_sweeper._metric_inc", MagicMock()),
    ):
        from backend.utils.stuck_ride_sweeper import _sweep

        await _sweep()

    ws_mock.assert_awaited_once()
    message, channel = ws_mock.await_args.args
    assert channel == "rider_rider-nd"
    assert message["type"] == "ride_cancelled"
    assert message["reason"] == "no_drivers_found"
    assert message["cancellation_type"] == "no_drivers_found"

    push_mock.assert_awaited_once()
    data = push_mock.await_args.args[3]
    assert data == {"ride_id": "ride-stuck-nd", "type": "ride_cancelled", "cancellation_type": "no_drivers_found"}
