"""Unit tests for the admin WebSocket-health primitives.

Covers the two helpers behind GET /api/admin/monitoring/websockets:
  - ConnectionManager.connection_stats(): per-replica socket counts bucketed
    by client type.
  - _WSPubSub.status(): cross-replica fan-out health snapshot, including the
    "configured but not connected" degraded state (Redis unreachable) that
    leaves the admin live map local-only — and the guarantee that the
    snapshot never leaks the password-bearing Redis URL.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Stub heavy deps before any backend import (mirrors test_p3_ws_broadcast).
_STUBS = ["loguru", "logging_utils", "fastapi", "fastapi.websockets"]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()
sys.modules["loguru"].logger = MagicMock()
sys.modules["fastapi"].WebSocket = MagicMock()
sys.modules["logging_utils"].diag_logger = MagicMock()

from backend.socket_manager import ConnectionManager  # noqa: E402
from backend.utils.ws_pubsub import CHANNEL, _WSPubSub  # noqa: E402

# ── connection_stats ──────────────────────────────────────────────────────


def test_connection_stats_empty():
    mgr = ConnectionManager()
    assert mgr.connection_stats() == {"total": 0, "admins": 0, "drivers": 0, "riders": 0}


def test_connection_stats_buckets_by_client_type():
    mgr = ConnectionManager()
    # Keys are {client_type}_{user_id} exactly as routes/websocket.py builds them.
    for key in ("admin_a1", "admin_a2", "driver_d1", "rider_r1", "rider_r2", "rider_r3"):
        mgr.active_connections[key] = MagicMock()

    stats = mgr.connection_stats()
    assert stats == {"total": 6, "admins": 2, "drivers": 1, "riders": 3}


def test_connection_stats_counts_unknown_prefix_in_total_only():
    mgr = ConnectionManager()
    mgr.active_connections["weird_key"] = MagicMock()
    stats = mgr.connection_stats()
    assert stats["total"] == 1
    assert stats["admins"] == stats["drivers"] == stats["riders"] == 0


# ── _WSPubSub.status ──────────────────────────────────────────────────────


def test_status_unconfigured_single_machine():
    ps = _WSPubSub()
    s = ps.status()
    assert s["active"] is False
    assert s["configured"] is False
    assert s["channel"] == CHANNEL
    assert s["backend_scheme"] == ""
    assert s["last_error"] is None


def test_status_configured_but_disconnected_reports_reason():
    ps = _WSPubSub()
    # Simulate a failed connect: URL was set, error recorded, no live task.
    ps._url = "rediss://default:secretpassword@redis.example.com:6379"
    ps._last_error = "connect: TimeoutError"

    s = ps.status()
    assert s["active"] is False
    assert s["configured"] is True
    assert s["backend_scheme"] == "rediss"
    assert s["last_error"] == "connect: TimeoutError"
    # The snapshot must never leak the password-bearing URL.
    assert "secretpassword" not in str(s)


def test_active_requires_live_subscription():
    """Regression for the Codex finding: after a failed reconnect drops the
    subscription, `_pubsub` is None but the consumer task keeps looping in
    backoff. `active` must be False (degraded), not True ("Live")."""
    ps = _WSPubSub()
    ps._redis = MagicMock()
    task = MagicMock()
    task.done.return_value = False
    ps._task = task

    ps._pubsub = None  # reconnect closed the subscription, consumer still looping
    assert ps.active is False

    ps._pubsub = MagicMock()  # resubscribed
    assert ps.active is True


@pytest.mark.anyio
async def test_start_reports_configured_when_connect_fails(monkeypatch):
    """Regression for the Codex finding: a configured-but-unreachable Redis
    must report configured=True (so the degraded banner shows), not be
    mislabelled single-machine because `_url` was only set on success."""

    async def _bad_ping():
        raise OSError("redis unreachable")

    bad_client = MagicMock()
    bad_client.ping = _bad_ping

    fake_asyncio = MagicMock()
    fake_asyncio.from_url = MagicMock(return_value=bad_client)
    monkeypatch.setitem(sys.modules, "redis", MagicMock())
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_asyncio)

    ps = _WSPubSub()
    ok = await ps.start(MagicMock(), "rediss://default:secretpassword@redis.example.com:6379")

    assert ok is False
    s = ps.status()
    assert s["configured"] is True
    assert s["active"] is False
    assert s["backend_scheme"] == "rediss"
    assert s["last_error"].startswith("connect:")
    assert "secretpassword" not in str(s)
