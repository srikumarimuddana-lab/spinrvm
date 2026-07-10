"""Perf/correctness tests for dispatch (#4): batched offer_skip + retry cap."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_redis_mget_aligns_results_and_handles_empty():
    from backend.utils import redis_client as rc

    async def go():
        await rc.redis_set("spinr:test:k1", "v1")
        await rc.redis_set("spinr:test:k2", "v2")
        return await rc.redis_mget(["spinr:test:k1", "spinr:test:missing", "spinr:test:k2"])

    vals = asyncio.run(go())
    assert vals[0] == "v1"
    assert vals[1] is None
    assert vals[2] == "v2"
    assert asyncio.run(rc.redis_mget([])) == []  # empty input → no round-trip


def test_dispatch_retry_stops_at_attempt_cap():
    """Past the cap, _dispatch_retry must not re-query or re-dispatch — the
    stuck-ride sweeper owns resolution from there."""
    from backend.routes import rides

    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock()) as get_ride,
        patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()) as match,
    ):
        asyncio.run(rides._dispatch_retry("r1", delay=0, attempt=rides._MAX_DISPATCH_ATTEMPTS + 1))

    get_ride.assert_not_awaited()
    match.assert_not_awaited()


def test_dispatch_retry_continues_below_cap_and_threads_attempt():
    from backend.routes import rides

    searching = {"id": "r1", "status": "searching"}
    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=searching)),
        patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()) as match,
    ):
        asyncio.run(rides._dispatch_retry("r1", delay=0, attempt=3))

    match.assert_awaited_once()
    assert match.await_args.kwargs["attempt"] == 3  # attempt threaded through
