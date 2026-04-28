"""Regression tests for B-P3-1: concurrent asyncio.gather broadcast.

Verifies:
  - broadcast() fans out to all connections simultaneously via asyncio.gather
  - A slow consumer (timeout) does not delay delivery to fast consumers
  - A failed connection is logged but does not abort the broadcast
  - Concurrent disconnect during broadcast does not raise RuntimeError
  - _deliver_broadcast_local() filters by connection-key prefix
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub heavy deps before any backend import.
# Track whether fastapi was already imported (real module) before this file
# is collected. If it was, we must NOT overwrite its WebSocket attribute —
# doing so corrupts the real fastapi module for the rest of the test session
# (routes/websocket.py would get a MagicMock WebSocket and FastAPI's
# dependency analysis would fail with FastAPIError on all subsequent tests).
_fastapi_was_imported = "fastapi" in sys.modules

_STUBS = [
    "loguru",
    "logging_utils",
    "fastapi",
    "fastapi.websockets",
]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

# Make `from loguru import logger` return mock.
sys.modules["loguru"].logger = MagicMock()
# Only patch fastapi.WebSocket when fastapi is a stub we just created.
# If the real fastapi is already loaded, patching it globally breaks every
# test that imports routes after this file is collected.
if not _fastapi_was_imported:
    sys.modules["fastapi"].WebSocket = MagicMock()
sys.modules["logging_utils"].diag_logger = MagicMock()

from backend.socket_manager import ConnectionManager  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(*client_ids: str) -> tuple[ConnectionManager, list[AsyncMock]]:
    """Return a ConnectionManager pre-populated with AsyncMock websockets."""
    mgr = ConnectionManager()
    mocks = []
    for cid in client_ids:
        ws = AsyncMock()
        mgr.active_connections[cid] = ws
        mocks.append(ws)
    return mgr, mocks


# ---------------------------------------------------------------------------
# broadcast() tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_broadcast_sends_to_all():
    """Every registered connection receives the message."""
    mgr, (ws1, ws2, ws3) = _make_manager("rider_1", "driver_1", "admin_1")
    msg = {"type": "surge_update", "multiplier": 1.5}

    await mgr.broadcast(msg)

    ws1.send_json.assert_awaited_once_with(msg)
    ws2.send_json.assert_awaited_once_with(msg)
    ws3.send_json.assert_awaited_once_with(msg)


@pytest.mark.anyio
async def test_broadcast_empty_connections_returns_silently():
    """broadcast() with no active connections does not raise."""
    mgr = ConnectionManager()
    # Should return without any exception
    await mgr.broadcast({"type": "ping"})


@pytest.mark.anyio
async def test_broadcast_slow_consumer_does_not_block_fast_consumers():
    """A slow consumer that exceeds the 2-second timeout does not delay others.

    Uses a patched timeout of 0.05 s so the test completes in milliseconds.
    """
    mgr = ConnectionManager()

    fast_ws = AsyncMock()

    async def _slow_send(msg):
        await asyncio.sleep(60)  # far beyond any reasonable timeout

    slow_ws = MagicMock()
    slow_ws.send_json = _slow_send

    mgr.active_connections = {"rider_fast": fast_ws, "rider_slow": slow_ws}

    # Patch timeout to 0.05 s so the test is fast while still exercising the
    # timeout path.
    real_wait_for = asyncio.wait_for

    async def patched_wait_for(coro, timeout):
        return await real_wait_for(coro, timeout=0.05)

    with patch("asyncio.wait_for", side_effect=patched_wait_for):
        import time

        t0 = time.monotonic()
        await mgr.broadcast({"type": "dispatch_offer"})
        elapsed = time.monotonic() - t0

    # Fast consumer received the message.
    fast_ws.send_json.assert_awaited_once()
    # Total time should not exceed 2 × timeout + overhead.
    assert elapsed < 1.0, f"broadcast took {elapsed:.3f}s — slow consumer blocked it"


@pytest.mark.anyio
async def test_broadcast_failed_connection_logged_not_raised():
    """An exception from one connection is logged; others still receive the message."""
    mgr, (ws_ok,) = _make_manager("rider_ok")

    ws_fail = AsyncMock()
    ws_fail.send_json.side_effect = RuntimeError("Connection reset by peer")
    mgr.active_connections["rider_fail"] = ws_fail

    with patch("backend.socket_manager.logger") as mock_log:
        await mgr.broadcast({"type": "test"})

    # OK connection received the message.
    ws_ok.send_json.assert_awaited_once()
    # Warning was logged for the failed connection.
    assert mock_log.warning.called


@pytest.mark.anyio
async def test_broadcast_asyncio_timeout_on_connection_logged():
    """asyncio.TimeoutError from a hung socket is caught and logged, not raised."""
    mgr = ConnectionManager()

    hung_ws = AsyncMock()
    hung_ws.send_json.side_effect = asyncio.TimeoutError()
    ok_ws = AsyncMock()

    mgr.active_connections = {"rider_hung": hung_ws, "rider_ok": ok_ws}

    with patch("backend.socket_manager.logger") as mock_log:
        await mgr.broadcast({"type": "ping"})

    ok_ws.send_json.assert_awaited_once()
    assert mock_log.warning.called


@pytest.mark.anyio
async def test_broadcast_concurrent_disconnect_does_not_raise():
    """Connections removed mid-broadcast do not cause RuntimeError.

    The snapshot taken before asyncio.gather ensures iteration is over a
    stable list even when concurrent code removes entries from active_connections.
    """
    mgr = ConnectionManager()

    ws_a = AsyncMock()
    ws_b = AsyncMock()

    removals = []
    send_a_calls = []

    original_send = ws_a.send_json

    async def _send_and_pop(msg):
        send_a_calls.append(msg)
        # Simulate a concurrent disconnect of ws_b while ws_a is sending.
        popped = mgr.active_connections.pop("conn_b", None)
        if popped:
            removals.append("conn_b")
        return await original_send(msg)

    ws_a.send_json = _send_and_pop

    mgr.active_connections = {"conn_a": ws_a, "conn_b": ws_b}

    # Must not raise RuntimeError: dictionary changed size during iteration
    await mgr.broadcast({"type": "test"})

    # Both connections were in the snapshot so both should have been attempted.
    assert len(send_a_calls) == 1  # our custom send was called once
    ws_b.send_json.assert_awaited_once()


# ---------------------------------------------------------------------------
# _deliver_broadcast_local() tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_deliver_broadcast_local_prefix_filter():
    """Only connections whose key starts with the given prefix receive the message."""
    mgr, (admin_ws,) = _make_manager("admin_u1")

    rider_ws = AsyncMock()
    driver_ws = AsyncMock()
    mgr.active_connections["rider_u2"] = rider_ws
    mgr.active_connections["driver_u3"] = driver_ws

    msg = {"type": "admin_alert"}
    await mgr._deliver_broadcast_local("admin_", msg)

    admin_ws.send_json.assert_awaited_once_with(msg)
    rider_ws.send_json.assert_not_called()
    driver_ws.send_json.assert_not_called()


@pytest.mark.anyio
async def test_deliver_broadcast_local_empty_prefix_match():
    """_deliver_broadcast_local() with no matching connections returns silently."""
    mgr, (rider_ws,) = _make_manager("rider_1")

    await mgr._deliver_broadcast_local("admin_", {"type": "admin_alert"})

    rider_ws.send_json.assert_not_called()


@pytest.mark.anyio
async def test_deliver_broadcast_local_multiple_admins():
    """All admin connections receive the prefix-scoped broadcast."""
    mgr, (a1, a2, a3) = _make_manager("admin_alice", "admin_bob", "admin_carol")
    rider_ws = AsyncMock()
    mgr.active_connections["rider_x"] = rider_ws

    msg = {"type": "driver_status_changed", "driver_id": "d1", "is_online": False}
    await mgr._deliver_broadcast_local("admin_", msg)

    a1.send_json.assert_awaited_once_with(msg)
    a2.send_json.assert_awaited_once_with(msg)
    a3.send_json.assert_awaited_once_with(msg)
    rider_ws.send_json.assert_not_called()
