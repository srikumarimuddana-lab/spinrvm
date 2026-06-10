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
async def test_resolve_active_ride_returns_first_row():
    driver_id = "drv-cache-single"
    rows = [_ride(driver_id), _ride(driver_id, status="driver_arrived")]
    with patch.object(breadcrumbs.db_supabase, "get_rows", AsyncMock(return_value=rows)):
        ride = await breadcrumbs.resolve_active_ride(driver_id)
    assert ride == rows[0]
