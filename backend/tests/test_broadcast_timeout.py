"""
B-P3-1: tests for socket_manager.broadcast() and _deliver_broadcast_local()
per-message timeout.

Contract:
  - A single stuck WebSocket connection (send_json that never returns) must
    NOT block the iteration over remaining connections.
  - The stuck connection's failure is logged at warning level.
  - The other connections still receive the message.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_conn(send_delay: float = 0.0):
    """Build a fake WebSocket whose send_json takes `send_delay` seconds."""
    conn = MagicMock()

    async def slow_send(msg):
        if send_delay > 0:
            await asyncio.sleep(send_delay)

    conn.send_json = AsyncMock(side_effect=slow_send)
    return conn


@pytest.mark.anyio
async def test_broadcast_skips_stuck_connection(monkeypatch):
    """A connection whose send hangs longer than the timeout must be
    skipped without stalling the rest."""
    # Shrink timeout for the test so we don't wait 2s.
    import socket_manager as sm
    from socket_manager import ConnectionManager

    monkeypatch.setattr(sm, "_BROADCAST_SEND_TIMEOUT", 0.05)

    mgr = ConnectionManager()
    fast_a = _make_conn(send_delay=0.0)
    stuck = _make_conn(send_delay=10.0)  # would block long after the test
    fast_b = _make_conn(send_delay=0.0)

    mgr.active_connections = {"rider_a": fast_a, "rider_stuck": stuck, "rider_b": fast_b}

    started = asyncio.get_event_loop().time()
    await mgr.broadcast({"type": "ping"})
    elapsed = asyncio.get_event_loop().time() - started

    # Even with a stuck socket, the call should return promptly.
    assert elapsed < 1.0, f"broadcast stalled for {elapsed:.2f}s — timeout not enforced"
    fast_a.send_json.assert_awaited_once()
    fast_b.send_json.assert_awaited_once()


@pytest.mark.anyio
async def test_local_broadcast_skips_stuck_connection(monkeypatch):
    """Same contract for the prefix-scoped local fan-out used by
    broadcast_to_admins fallback."""
    import socket_manager as sm
    from socket_manager import ConnectionManager

    monkeypatch.setattr(sm, "_BROADCAST_SEND_TIMEOUT", 0.05)

    mgr = ConnectionManager()
    admin_fast = _make_conn(send_delay=0.0)
    admin_stuck = _make_conn(send_delay=10.0)
    rider_other = _make_conn(send_delay=0.0)  # different prefix — must be skipped

    mgr.active_connections = {
        "admin_a": admin_fast,
        "admin_stuck": admin_stuck,
        "rider_b": rider_other,
    }

    started = asyncio.get_event_loop().time()
    await mgr._deliver_broadcast_local("admin_", {"type": "ping"})
    elapsed = asyncio.get_event_loop().time() - started

    assert elapsed < 1.0
    admin_fast.send_json.assert_awaited_once()
    rider_other.send_json.assert_not_awaited()


@pytest.mark.anyio
async def test_local_broadcast_fans_out_concurrently():
    """Admin fan-out must dispatch sends concurrently, not serially, so a single
    slow admin socket can't delay delivery to the others. Verified by probing
    peak in-flight concurrency (deterministic) rather than wall-clock timing.
    """
    from socket_manager import ConnectionManager

    active = 0
    max_active = 0

    async def _probe_send(_msg):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1

    mgr = ConnectionManager()
    conns = {}
    for i in range(4):
        c = MagicMock()
        c.send_json = AsyncMock(side_effect=_probe_send)
        conns[f"admin_{i}"] = c
    mgr.active_connections = conns

    await mgr._deliver_broadcast_local("admin_", {"type": "ride_status_changed"})

    assert max_active == 4, (
        f"admin fan-out peaked at {max_active} concurrent sends; expected 4 — "
        "sequential awaits delay every admin behind the slowest socket"
    )


@pytest.mark.anyio
async def test_broadcast_propagates_message_to_all_healthy_connections():
    """Sanity check: when no connection is stuck, every connection gets
    the message exactly once."""
    from socket_manager import ConnectionManager

    mgr = ConnectionManager()
    a = _make_conn()
    b = _make_conn()
    c = _make_conn()
    mgr.active_connections = {"rider_a": a, "rider_b": b, "rider_c": c}

    payload = {"type": "ride_status_changed", "status": "completed"}
    await mgr.broadcast(payload)

    a.send_json.assert_awaited_once_with(payload)
    b.send_json.assert_awaited_once_with(payload)
    c.send_json.assert_awaited_once_with(payload)
