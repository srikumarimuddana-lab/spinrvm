"""Regression: a Redis outage on the ephemeral driver-location cache must
not block the authoritative DB write of the driver's coordinates.

Bug (redis driver display): ``ConnectionManager.update_driver_location``
called ``redis_set`` with no error handling, and ``redis_set`` re-raises
when ``REDIS_URL`` is configured but Redis is unreachable. In the driver
WebSocket location handler that cache write ran BEFORE the durable
``drivers`` table write, so a Redis blip raised and skipped persistence —
leaving an online driver with no lat/lng and no car marker on the admin
live-monitoring map.

Contract:
  - ``update_driver_location`` swallows a Redis failure (logs, no raise),
    so the caller's downstream DB write still executes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.anyio
async def test_update_driver_location_swallows_redis_error(monkeypatch):
    """A raising redis_set must not propagate out of the cache write."""
    from socket_manager import ConnectionManager

    failing = AsyncMock(side_effect=RuntimeError("Redis configured but unavailable"))
    # redis_set is imported lazily inside the method from utils.redis_client.
    import utils.redis_client as rc

    monkeypatch.setattr(rc, "redis_set", failing)

    mgr = ConnectionManager()

    # Must NOT raise — the ephemeral cache is best-effort.
    await mgr.update_driver_location("driver-123", 50.4452, -104.6189)

    assert failing.await_count == 1


@pytest.mark.anyio
async def test_update_driver_location_writes_cache_when_redis_ok(monkeypatch):
    """Happy path still writes the 60 s location cache key."""
    import utils.redis_client as rc
    from socket_manager import ConnectionManager

    ok = AsyncMock(return_value=None)
    monkeypatch.setattr(rc, "redis_set", ok)

    mgr = ConnectionManager()
    await mgr.update_driver_location("driver-123", 50.4452, -104.6189)

    ok.assert_awaited_once()
    key = ok.await_args.args[0]
    assert key == "spinr:driver:location:driver-123"
