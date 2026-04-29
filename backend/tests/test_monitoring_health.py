"""Unit tests for GET /admin/monitoring/health.

Pins:
  - Returns status 'ok' when DB circuit is closed and Redis is connected
  - Returns status 'degraded' when circuit is half-open
  - Returns status 'degraded' when Redis is disconnected
  - Returns 503 when DB circuit is open
  - Operational counts (active_rides, online_drivers) are included
  - Query errors in operational counts don't change overall status
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_breaker(state: str = "closed", failures: int = 0) -> MagicMock:
    b = MagicMock()
    b._state = state
    b._failure_times = list(range(failures))
    return b


def _redis_stats(connected: bool = True) -> dict:
    return {"connected": connected, "used_memory_bytes": 1024, "total_keys": 10}


def _count_res(n: int) -> MagicMock:
    res = MagicMock()
    res.count = n
    return res


@pytest.mark.anyio
async def test_health_ok():
    from backend.routes.admin.monitoring import get_dashboard_health

    with (
        patch("backend.routes.admin.monitoring._db_breaker", _make_breaker("closed")),
        patch(
            "backend.routes.admin.monitoring.get_redis_stats", new_callable=AsyncMock, return_value=_redis_stats(True)
        ),
        patch("backend.routes.admin.monitoring.run_sync", new_callable=AsyncMock, return_value=_count_res(3)),
    ):
        result = await get_dashboard_health(current_admin={"id": "admin-1"})

    assert result["status"] == "ok"
    assert result["checks"]["db"]["status"] == "ok"
    assert result["checks"]["redis"]["status"] == "ok"
    assert result["checks"]["operations"]["active_rides"] == 3


@pytest.mark.anyio
async def test_health_degraded_half_open():
    from backend.routes.admin.monitoring import get_dashboard_health

    with (
        patch("backend.routes.admin.monitoring._db_breaker", _make_breaker("half-open", failures=2)),
        patch(
            "backend.routes.admin.monitoring.get_redis_stats", new_callable=AsyncMock, return_value=_redis_stats(True)
        ),
        patch("backend.routes.admin.monitoring.run_sync", new_callable=AsyncMock, return_value=_count_res(0)),
    ):
        result = await get_dashboard_health(current_admin={"id": "admin-1"})

    assert result["status"] == "degraded"
    assert result["checks"]["db"]["status"] == "degraded"
    assert result["checks"]["db"]["recent_failures"] == 2


@pytest.mark.anyio
async def test_health_degraded_redis_disconnected():
    from backend.routes.admin.monitoring import get_dashboard_health

    with (
        patch("backend.routes.admin.monitoring._db_breaker", _make_breaker("closed")),
        patch(
            "backend.routes.admin.monitoring.get_redis_stats", new_callable=AsyncMock, return_value=_redis_stats(False)
        ),
        patch("backend.routes.admin.monitoring.run_sync", new_callable=AsyncMock, return_value=_count_res(1)),
    ):
        result = await get_dashboard_health(current_admin={"id": "admin-1"})

    assert result["status"] == "degraded"
    assert result["checks"]["redis"]["connected"] is False


@pytest.mark.anyio
async def test_health_down_circuit_open():
    from fastapi import HTTPException

    from backend.routes.admin.monitoring import get_dashboard_health

    with (
        patch("backend.routes.admin.monitoring._db_breaker", _make_breaker("open", failures=5)),
        patch(
            "backend.routes.admin.monitoring.get_redis_stats", new_callable=AsyncMock, return_value=_redis_stats(True)
        ),
        patch("backend.routes.admin.monitoring.run_sync", new_callable=AsyncMock, return_value=_count_res(0)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_dashboard_health(current_admin={"id": "admin-1"})

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["status"] == "down"


@pytest.mark.anyio
async def test_health_operational_query_error_does_not_change_status():
    from backend.routes.admin.monitoring import get_dashboard_health

    with (
        patch("backend.routes.admin.monitoring._db_breaker", _make_breaker("closed")),
        patch(
            "backend.routes.admin.monitoring.get_redis_stats", new_callable=AsyncMock, return_value=_redis_stats(True)
        ),
        patch(
            "backend.routes.admin.monitoring.run_sync", new_callable=AsyncMock, side_effect=Exception("query timeout")
        ),
    ):
        result = await get_dashboard_health(current_admin={"id": "admin-1"})

    assert result["status"] == "ok"
    assert result["checks"]["operations"]["active_rides"] is None
    assert result["checks"]["operations"]["online_drivers"] is None
