"""B3.1 — resolve_active_rides_cached: 5s Redis cache on the GPS-ping hot path.

The WS location handler calls this on every driver ping; the contract is
at most one rides query per driver per TTL window, including for idle
drivers (empty result must be cached too), with Redis failure degrading
to the original per-ping query.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import breadcrumbs


def _ride(driver_id: str, status: str = "in_progress") -> dict:
    return {
        "id": f"ride-{driver_id}",
        "driver_id": driver_id,
        "status": status,
        "created_at": "2026-06-10T12:00:00+00:00",
    }


@pytest.mark.anyio
async def test_second_call_within_ttl_skips_db():
    driver_id = "drv-cache-hit"
    rows = [_ride(driver_id)]
    get_rows = AsyncMock(return_value=rows)
    with patch.object(breadcrumbs.db_supabase, "get_rows", get_rows):
        first = await breadcrumbs.resolve_active_rides_cached(driver_id)
        second = await breadcrumbs.resolve_active_rides_cached(driver_id)

    assert first == rows
    assert second == rows
    assert get_rows.await_count == 1


@pytest.mark.anyio
async def test_empty_result_is_cached_for_idle_drivers():
    driver_id = "drv-cache-idle"
    get_rows = AsyncMock(return_value=[])
    with patch.object(breadcrumbs.db_supabase, "get_rows", get_rows):
        assert await breadcrumbs.resolve_active_rides_cached(driver_id) == []
        assert await breadcrumbs.resolve_active_rides_cached(driver_id) == []

    assert get_rows.await_count == 1


@pytest.mark.anyio
async def test_redis_failure_degrades_to_db_query():
    driver_id = "drv-cache-redisdown"
    rows = [_ride(driver_id)]
    get_rows = AsyncMock(return_value=rows)
    boom = AsyncMock(side_effect=RuntimeError("redis down"))
    with (
        patch.object(breadcrumbs.db_supabase, "get_rows", get_rows),
        patch("backend.utils.breadcrumbs.redis_get", boom),
        patch("backend.utils.breadcrumbs.redis_set", boom),
    ):
        assert await breadcrumbs.resolve_active_rides_cached(driver_id) == rows
        assert await breadcrumbs.resolve_active_rides_cached(driver_id) == rows

    assert get_rows.await_count == 2


@pytest.mark.anyio
async def test_invalidation_busts_a_cached_empty_list():
    """Codex P2 (PR #1758): an idle driver's cached [] must not survive ride
    assignment — assignment/acceptance invalidate so the first post-accept
    pings attach to the ride and reach the rider."""
    driver_id = "drv-cache-invalidate"
    get_rows = AsyncMock(side_effect=[[], [_ride(driver_id, status="driver_accepted")]])
    with patch.object(breadcrumbs.db_supabase, "get_rows", get_rows):
        assert await breadcrumbs.resolve_active_rides_cached(driver_id) == []
        await breadcrumbs.invalidate_active_rides_cache(driver_id)
        rides = await breadcrumbs.resolve_active_rides_cached(driver_id)

    assert rides and rides[0]["status"] == "driver_accepted"
    assert get_rows.await_count == 2


@pytest.mark.anyio
async def test_dispatch_assignment_invalidates_the_cache():
    from backend.services.dispatch_service import DispatchService

    db = AsyncMock()
    invalidate = AsyncMock()
    with patch("backend.services.dispatch_service.invalidate_active_rides_cache", invalidate):
        await DispatchService(db).assign_driver_to_ride("ride-1", "drv-1", now="2026-06-10T12:00:00+00:00")

    db.update_one.assert_awaited_once()
    invalidate.assert_awaited_once_with("drv-1")


@pytest.mark.anyio
async def test_resolve_active_ride_returns_first_row():
    driver_id = "drv-cache-single"
    rows = [_ride(driver_id), _ride(driver_id, status="driver_arrived")]
    with patch.object(breadcrumbs.db_supabase, "get_rows", AsyncMock(return_value=rows)):
        ride = await breadcrumbs.resolve_active_ride(driver_id)
    assert ride == rows[0]
