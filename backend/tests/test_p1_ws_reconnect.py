"""
P1-6: WebSocket reconnect with state preservation (C8)

The server does not buffer WS events. Any `driver_accepted`, `ride_started`,
etc. messages sent while a client is disconnected are permanently lost.
The documented recovery path is:

  1. Client reconnects + sends `auth`.
  2. Client immediately calls `GET /rides/active` (rider) or the driver
     active-ride poll to re-sync any missed status transitions.

These tests pin:
  a) ConnectionManager round-trips (disconnect removes, reconnect re-adds).
  b) `GET /rides/active` returns an in-progress ride so the client can
     recover state without a WS replay buffer.
  c) Messages sent to a disconnected client are silently dropped (no crash)
     — the caller does NOT need to know whether the client is online.

Run:
    pytest backend/tests/test_p1_ws_reconnect.py -v
    pytest -m e2e backend/tests/test_p1_ws_reconnect.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

# Minimal starlette Request used by direct function calls in TestActiveRideHttpRecovery
# (SlowAPI requires an actual starlette.requests.Request, not a MagicMock).
_HTTP_SCOPE: dict = {
    "type": "http",
    "method": "GET",
    "path": "/rides/active",
    "headers": [],
    "query_string": b"",
    "root_path": "",
}
_MOCK_REQUEST = StarletteRequest(_HTTP_SCOPE)

RIDER_ID = "rider_p1_6"
DRIVER_USER_ID = "driver_user_p1_6"
DRIVER_ID = "driver_row_p1_6"
RIDE_ID = "ride_p1_6_001"


def _ride(status: str, driver_id: str | None = DRIVER_ID, **extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": status,
        "driver_id": driver_id,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.12,
        "dropoff_lng": -106.65,
        "pickup_address": "123 Main St",
        "dropoff_address": "456 Broadway",
        "estimated_fare": 18.5,
        "total_fare": 18.5,
        "grand_total": 18.5,
        "distance_km": 3.2,
        "duration_minutes": 8,
        "payment_method": "card",
        "payment_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(extra)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# ConnectionManager round-trip
# ─────────────────────────────────────────────────────────────────────────────


class TestConnectionManagerReconnect:
    """Pins the connect → disconnect → reconnect lifecycle of ConnectionManager.

    Code under test: backend/socket_manager.py::ConnectionManager.
    """

    @pytest.mark.asyncio
    async def test_connect_registers_client(self):
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = MagicMock()
        await mgr.connect(ws, "rider_abc")
        assert "rider_abc" in mgr.active_connections
        assert mgr.active_connections["rider_abc"] is ws

    @pytest.mark.asyncio
    async def test_disconnect_removes_client(self):
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = MagicMock()
        await mgr.connect(ws, "rider_abc")
        mgr.disconnect("rider_abc")
        assert "rider_abc" not in mgr.active_connections

    @pytest.mark.asyncio
    async def test_reconnect_replaces_previous_socket(self):
        """A second connect() for the same client_id overwrites the old socket —
        the backend always sends to the most-recent connection."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws1, ws2 = MagicMock(), MagicMock()
        await mgr.connect(ws1, "rider_abc")
        await mgr.connect(ws2, "rider_abc")
        assert mgr.active_connections["rider_abc"] is ws2

    @pytest.mark.asyncio
    async def test_reconnect_closes_stale_connection_with_replaced_code(self):
        """Ranked blocker #15: the OLD connection must get an explicit close
        signal (not just be silently dropped from the registry) when a second
        device/tab registers under the same client_id, so a stale tab doesn't
        sit open believing it's still receiving live updates."""
        from backend.socket_manager import WS_CLOSE_REPLACED_BY_NEW_CONNECTION, ConnectionManager

        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        await mgr.connect(ws1, "rider_abc")
        await mgr.connect(ws2, "rider_abc")

        # New connection is the one left registered and receiving fan-out.
        assert mgr.active_connections["rider_abc"] is ws2
        # Old connection gets an explicit close with the replaced-connection code.
        ws1.close.assert_awaited_once_with(
            code=WS_CLOSE_REPLACED_BY_NEW_CONNECTION, reason="replaced_by_new_connection"
        )
        # The new connection must never be touched by the old-connection cleanup.
        ws2.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reconnect_when_old_connection_already_dead_does_not_raise(self):
        """The old socket may already be gone (network drop, backgrounded app)
        by the time the second device connects — closing it must not raise or
        block the new connection from being registered."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        ws1.close = AsyncMock(side_effect=RuntimeError('Cannot call "close" once a close message has been sent'))
        await mgr.connect(ws1, "rider_abc")

        # Must not raise despite the old socket's close() blowing up.
        await mgr.connect(ws2, "rider_abc")

        assert mgr.active_connections["rider_abc"] is ws2
        ws1.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_first_connect_has_no_old_socket_to_close(self):
        """The very first connect() for a client_id has no predecessor —
        must not attempt to close anything."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, "rider_abc")
        ws.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_to_disconnected_client_does_not_raise(self):
        """Server sends a WS event to a disconnected client — must not crash.

        This is the C8 scenario: the client drops, the server tries to push
        `ride_started`, and we verify it's silently dropped rather than
        raising an exception that could abort the ride handler.
        """
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        # Client was connected, but now is not.
        await mgr._deliver_local({"type": "ride_started", "ride_id": RIDE_ID}, "rider_abc")
        # No exception = pass.

    @pytest.mark.asyncio
    async def test_send_personal_message_falls_back_to_local_when_pubsub_disabled(self):
        """When Redis pub/sub is unavailable, delivery falls back to local dict."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        await mgr.connect(ws, "rider_abc")

        with patch("backend.utils.ws_pubsub.pubsub.publish", AsyncMock(return_value=False)):
            await mgr.send_personal_message({"type": "ping"}, "rider_abc")

        ws.send_json.assert_awaited_once_with({"type": "ping"})


# ─────────────────────────────────────────────────────────────────────────────
# P1-6: HTTP recovery — GET /rides/active returns in-progress ride
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestActiveRideHttpRecovery:
    """Pins `GET /rides/active` as the reconnect state-recovery mechanism.

    When a rider reconnects after a network drop, the client calls this
    endpoint to discover the current ride status (e.g. `driver_accepted`,
    `in_progress`) without waiting for the next WS event.

    Code under test: backend/routes/rides.py::get_active_ride (~line 835).
    """

    async def test_returns_in_progress_ride_with_driver(self):
        from backend.routes import rides as rides_mod

        ride = _ride(status="in_progress")
        driver_row = {
            "id": DRIVER_ID,
            "user_id": DRIVER_USER_ID,
            "rating": 4.9,
            "total_rides": 120,
            "vehicle_make": "Toyota",
            "vehicle_model": "Camry",
            "vehicle_color": "Silver",
            "license_plate": "ABC 123",
            "lat": 52.14,
            "lng": -106.68,
            "heading": 90,
        }
        driver_user = {
            "id": DRIVER_USER_ID,
            "first_name": "Jane",
            "last_name": "Driver",
        }

        with (
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(side_effect=[[], [ride]]),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value=driver_row),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value=driver_user),
            ),
        ):
            result = await rides_mod.get_active_ride(request=_MOCK_REQUEST, current_user={"id": RIDER_ID})

        assert result["active"] is True
        assert result["ride"]["id"] == RIDE_ID
        assert result["ride"]["status"] == "in_progress"
        assert result["ride"]["driver"]["name"] == "Jane Driver"

    async def test_returns_driver_accepted_ride(self):
        """The rider reconnects right after the driver accepted — must see
        driver_accepted so the app transitions away from 'searching'."""
        from backend.routes import rides as rides_mod

        ride = _ride(status="driver_accepted")

        with (
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(side_effect=[[], [ride]]),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value=None),
            ),
        ):
            result = await rides_mod.get_active_ride(request=_MOCK_REQUEST, current_user={"id": RIDER_ID})

        assert result["active"] is True
        assert result["ride"]["status"] == "driver_accepted"

    async def test_returns_false_when_no_active_ride(self):
        """After ride completes mid-disconnect, reconnect must not restore a
        stale ride — `active: False` signals the client to clear state."""
        from backend.routes import rides as rides_mod

        with patch(
            "backend.routes.rides._deps.db_supabase.get_rows",
            AsyncMock(return_value=[]),
        ):
            result = await rides_mod.get_active_ride(request=_MOCK_REQUEST, current_user={"id": RIDER_ID})

        assert result["active"] is False
        assert result["ride"] is None

    async def test_searching_ride_is_included_in_active(self):
        """A ride still in 'searching' is active from the rider's perspective —
        the client must not clear it while waiting for driver assignment."""
        from backend.routes import rides as rides_mod

        ride = _ride(status="searching", driver_id=None)

        with (
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(side_effect=[[], [ride]]),
            ),
        ):
            result = await rides_mod.get_active_ride(request=_MOCK_REQUEST, current_user={"id": RIDER_ID})

        assert result["active"] is True
        assert result["ride"]["status"] == "searching"
