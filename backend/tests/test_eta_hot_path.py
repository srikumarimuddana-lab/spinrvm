"""#1: WS hot-path ETA is cache-only; provider round-trip happens off-loop.

get_cached_ride_eta does a single Redis read (no OSRM/Google call), and
refresh_ride_eta (run via create_task) refreshes the cache without ever raising
into the receive loop.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_get_cached_ride_eta_hit_and_miss():
    from backend.utils import maps_eta
    from backend.utils import redis_client as rc

    async def go():
        await rc.redis_set("eta:dX:rX", "123")
        return (
            await maps_eta.get_cached_ride_eta("dX", "rX"),
            await maps_eta.get_cached_ride_eta("dNONE", "rNONE"),
        )

    hit, miss = asyncio.run(go())
    assert hit == 123  # served from cache, no provider call
    assert miss is None  # caller schedules refresh off-loop


def test_get_cached_ride_eta_never_calls_provider():
    from backend.utils import maps_eta

    # If the hot-path reader ever hit the routing provider this patch would fire.
    with patch("backend.utils.maps_eta._osrm_eta_seconds", AsyncMock(side_effect=AssertionError)):
        assert asyncio.run(maps_eta.get_cached_ride_eta("nope", "nope")) is None


def test_refresh_ride_eta_swallows_errors():
    from backend.utils import maps_eta

    with patch(
        "backend.utils.maps_eta.get_ride_eta_seconds",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        # Must not raise — it runs as a fire-and-forget create_task.
        asyncio.run(
            maps_eta.refresh_ride_eta(
                driver_lat=1.0,
                driver_lng=2.0,
                dest_lat=3.0,
                dest_lng=4.0,
                maps_api_key="",
                driver_id="d",
                ride_id="r",
            )
        )
