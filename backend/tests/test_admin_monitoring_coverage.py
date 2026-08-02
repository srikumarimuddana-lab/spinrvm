"""Additional unit-level coverage for backend/routes/admin/monitoring.py.

test_monitoring_health.py covers /health; test_email_deliverability.py covers
/email-deliverability + the profile-image backfill. This file covers the
remaining endpoints: live-map fetchers (drivers/rides), Redis
monitoring/flush, WebSocket health, and the infrastructure snapshot.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    import routes.admin.monitoring as monitoring
except ImportError:
    import backend.routes.admin.monitoring as monitoring  # type: ignore[no-redef]


def _res(rows):
    r = MagicMock()
    r.data = rows
    return r


# ---------------------------------------------------------------------------
# build_monitoring_ride
# ---------------------------------------------------------------------------


def test_build_monitoring_ride_shapes_full_row():
    ride = {
        "id": "r1",
        "status": "in_progress",
        "rider_id": "u1",
        "driver_id": "d1",
        "pickup_lat": 1.0,
        "pickup_lng": 2.0,
        "pickup_address": "A",
        "dropoff_lat": 3.0,
        "dropoff_lng": 4.0,
        "dropoff_address": "B",
        "total_fare": 12.5,
        "distance_km": 3.2,
        "created_at": "2026-01-01T00:00:00Z",
        "corporate_account_id": "corp-1",
        "driver_current_lat": 5.0,
        "driver_current_lng": 6.0,
        "is_scheduled": True,
    }
    rider = {"first_name": "Jane", "last_name": "Doe", "phone": "306-555-0100", "profile_image": "url"}
    driver_user = {"first_name": "Bob", "last_name": "Smith", "phone": "306-555-0200"}
    out = monitoring.build_monitoring_ride(ride, rider=rider, driver_user=driver_user, driver_row={"lat": 9, "lng": 9})
    assert out["rider_name"] == "Jane Doe"
    assert out["driver_name"] == "Bob Smith"
    assert out["is_corporate"] is True
    assert out["driver_lat"] == 5.0  # ride's own live location wins over driver_row
    # Finding #12: is_scheduled must survive into the monitoring payload so
    # ops can tell a scheduled ride's cancellation apart from an on-demand one.
    assert out["is_scheduled"] is True


def test_build_monitoring_ride_handles_missing_rider_and_driver():
    ride = {"id": "r1", "status": "searching", "created_at": None}
    out = monitoring.build_monitoring_ride(ride)
    assert out["rider_name"] == "Unknown"
    assert out["driver_name"] is None
    assert out["is_corporate"] is False
    assert out["created_at"] == "None"
    # Missing/falsy is_scheduled must coerce to False, not None or KeyError.
    assert out["is_scheduled"] is False


# ---------------------------------------------------------------------------
# fetch_monitoring_drivers / GET /drivers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_monitoring_drivers_empty_returns_empty_list():
    with patch.object(monitoring, "run_sync", AsyncMock(return_value=_res([]))):
        out = await monitoring.fetch_monitoring_drivers()
    assert out == []


@pytest.mark.anyio
async def test_fetch_monitoring_drivers_shapes_online_and_available():
    drivers = [
        {
            "id": "drv1",
            "user_id": "u1",
            "is_online": True,
            "is_available": True,
            "lat": 1.0,
            "lng": 2.0,
            "vehicle_make": "Toyota",
            "vehicle_model": "Camry",
            "vehicle_color": "Black",
            "license_plate": "ABC123",
            "vehicle_type_id": "sedan",
            "rating": 4.9,
            "total_rides": 100,
            "service_area_id": "area1",
            "went_online_at": "2026-01-01T00:00:00Z",
            "went_offline_at": None,
        }
    ]
    users = [{"id": "u1", "first_name": "Bob", "last_name": "S", "phone": "306-555-0200", "profile_image": None}]
    active_rides = [{"driver_id": "drv1", "id": "ride-1"}]

    run_sync_mock = AsyncMock(side_effect=[_res(drivers), _res(users), _res(active_rides)])
    with (
        patch.object(monitoring, "run_sync", run_sync_mock),
        patch.object(monitoring, "present_driver_ids", AsyncMock(return_value={"drv1"})),
        patch.object(monitoring, "intent_online", return_value=True),
    ):
        out = await monitoring.fetch_monitoring_drivers()

    assert len(out) == 1
    d = out[0]
    assert d["name"] == "Bob S"
    assert d["is_online"] is True
    assert d["is_available"] is True
    assert d["active_ride_id"] == "ride-1"


@pytest.mark.anyio
async def test_fetch_monitoring_drivers_offline_when_not_present():
    """A driver who intends online but has no Redis presence heartbeat must
    surface as offline — this is the whole point of the presence layer."""
    drivers = [{"id": "drv1", "user_id": "u1", "is_available": True}]
    run_sync_mock = AsyncMock(side_effect=[_res(drivers), _res([]), _res([])])
    with (
        patch.object(monitoring, "run_sync", run_sync_mock),
        patch.object(monitoring, "present_driver_ids", AsyncMock(return_value=set())),
        patch.object(monitoring, "intent_online", return_value=True),
    ):
        out = await monitoring.fetch_monitoring_drivers()
    assert out[0]["is_online"] is False
    assert out[0]["is_available"] is False


@pytest.mark.anyio
async def test_get_monitoring_drivers_endpoint_delegates_to_fetcher():
    with patch.object(monitoring, "fetch_monitoring_drivers", AsyncMock(return_value=[{"id": "d1"}])):
        out = await monitoring.get_monitoring_drivers(current_admin={"id": "admin"})
    assert out == [{"id": "d1"}]


# ---------------------------------------------------------------------------
# fetch_monitoring_rides / GET /rides
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_monitoring_rides_empty_returns_empty_list():
    with patch.object(monitoring, "run_sync", AsyncMock(return_value=_res([]))):
        out = await monitoring.fetch_monitoring_rides()
    assert out == []


@pytest.mark.anyio
async def test_fetch_monitoring_rides_joins_riders_and_drivers():
    rides = [{"id": "r1", "status": "in_progress", "rider_id": "u1", "driver_id": "drv1", "created_at": "t"}]
    riders = [{"id": "u1", "first_name": "Jane", "last_name": "Doe"}]
    drivers_map = [{"id": "drv1", "user_id": "du1", "lat": 1.0, "lng": 2.0}]
    driver_users = [{"id": "du1", "first_name": "Bob", "last_name": "S"}]

    run_sync_mock = AsyncMock(side_effect=[_res(rides), _res(riders), _res(drivers_map), _res(driver_users)])
    with patch.object(monitoring, "run_sync", run_sync_mock):
        out = await monitoring.fetch_monitoring_rides()

    assert len(out) == 1
    assert out[0]["rider_name"] == "Jane Doe"
    assert out[0]["driver_name"] == "Bob S"


@pytest.mark.anyio
async def test_get_monitoring_rides_endpoint_delegates_to_fetcher():
    with patch.object(monitoring, "fetch_monitoring_rides", AsyncMock(return_value=[{"id": "r1"}])):
        out = await monitoring.get_monitoring_rides(current_admin={"id": "admin"})
    assert out == [{"id": "r1"}]


# ---------------------------------------------------------------------------
# Redis monitoring
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_redis_health_computes_hit_rate_and_flushable_flags():
    stats = {"keyspace_hits_total": 80, "keyspace_misses_total": 20}
    prefix_counts = {"cache:user:": 5, "session:": 3, "__other__": 1}
    with (
        patch.object(monitoring, "get_redis_stats", AsyncMock(return_value=stats)),
        patch.object(monitoring, "count_keys_by_prefix", AsyncMock(return_value=prefix_counts)),
    ):
        out = await monitoring.get_redis_health(current_admin={"id": "admin"})

    assert out["stats"]["hit_rate_percent"] == 80.0
    by_prefix = {p["prefix"]: p for p in out["prefix_counts"]}
    assert by_prefix["cache:user:"]["flushable"] is True
    assert by_prefix["session:"]["flushable"] is False


@pytest.mark.anyio
async def test_get_redis_health_no_ops_hit_rate_is_none():
    with (
        patch.object(monitoring, "get_redis_stats", AsyncMock(return_value={})),
        patch.object(monitoring, "count_keys_by_prefix", AsyncMock(return_value={})),
    ):
        out = await monitoring.get_redis_health(current_admin={"id": "admin"})
    assert out["stats"]["hit_rate_percent"] is None


@pytest.mark.anyio
async def test_get_redis_connectivity_returns_probe_results():
    probe = [{"label": "REDIS_URL", "status": "ok"}]
    with patch("utils.redis_diag.diagnose_redis", AsyncMock(return_value=probe), create=True):
        out = await monitoring.get_redis_connectivity(current_admin={"id": "admin"})
    assert out == {"connectivity": probe}


@pytest.mark.anyio
async def test_get_redis_connectivity_probe_exception_is_caught():
    async def _raise(*a, **kw):
        raise RuntimeError("boom")

    with patch("utils.redis_diag.diagnose_redis", _raise, create=True):
        out = await monitoring.get_redis_connectivity(current_admin={"id": "admin"})
    assert out["connectivity"][0]["status"] == "error"


@pytest.mark.anyio
async def test_flush_redis_prefix_requires_confirm_flush():
    from fastapi import HTTPException

    body = monitoring.FlushPrefixRequest(prefix="cache:user:", confirm="nope")
    with pytest.raises(HTTPException) as ei:
        await monitoring.flush_redis_prefix(body, current_admin={"id": "admin"})
    assert ei.value.status_code == 400


@pytest.mark.anyio
async def test_flush_redis_prefix_rejects_non_allowlisted_prefix():
    from fastapi import HTTPException

    body = monitoring.FlushPrefixRequest(prefix="session:", confirm="FLUSH")
    with pytest.raises(HTTPException) as ei:
        await monitoring.flush_redis_prefix(body, current_admin={"id": "admin"})
    assert ei.value.status_code == 403


@pytest.mark.anyio
async def test_flush_redis_prefix_deletes_allowlisted_prefix():
    with patch.object(monitoring, "redis_delete_pattern", AsyncMock(return_value=12)):
        body = monitoring.FlushPrefixRequest(prefix="cache:user:", confirm="FLUSH")
        out = await monitoring.flush_redis_prefix(body, current_admin={"id": "admin-9"})
    assert out == {"prefix": "cache:user:", "deleted_keys": 12, "admin_id": "admin-9"}


# ---------------------------------------------------------------------------
# WebSocket health
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_websocket_health_returns_fanout_and_connections():
    fake_manager = MagicMock()
    fake_manager.connection_stats.return_value = {"total": 3, "admins": 1, "drivers": 1, "riders": 1}
    fake_pubsub = MagicMock()
    fake_pubsub.status.return_value = {"active": True, "configured": True}

    fake_socket_mod = MagicMock(manager=fake_manager)
    fake_ws_pubsub_mod = MagicMock(pubsub=fake_pubsub)

    with patch.dict(
        "sys.modules",
        {"socket_manager": fake_socket_mod, "utils.ws_pubsub": fake_ws_pubsub_mod},
    ):
        out = await monitoring.get_websocket_health(current_admin={"id": "admin"})

    assert out["fanout"] == {"active": True, "configured": True}
    assert out["connections"] == {"total": 3, "admins": 1, "drivers": 1, "riders": 1}
    assert out["per_worker"] is True


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_infrastructure_stats_returns_process_and_redis_summary():
    breaker = MagicMock()
    breaker._state = "closed"
    breaker._failure_times = []
    breaker._opened_at = None

    with (
        patch.object(monitoring, "_db_breaker", breaker),
        patch.object(
            monitoring,
            "get_redis_stats",
            AsyncMock(
                return_value={
                    "connected": True,
                    "used_memory_bytes": 1024,
                    "used_memory_human": "1.0K",
                    "maxmemory_human": "10M",
                    "used_memory_percent": 1.0,
                    "total_keys": 5,
                    "evicted_keys_total": 0,
                }
            ),
        ),
        patch.object(monitoring, "_metrics_snapshot", return_value={"counters": {"spinr_x_total": {"a": 1, "b": 2}}}),
    ):
        out = await monitoring.get_infrastructure_stats(current_admin={"id": "admin"})

    assert out["db_circuit_breaker"]["state"] == "closed"
    assert out["redis"]["connected"] is True
    assert out["metrics"]["spinr_x_total"] == 3


def test_humanize_bytes_local_scales_units():
    assert monitoring._humanize_bytes_local(None) is None
    assert monitoring._humanize_bytes_local(500) == "500B"
    assert monitoring._humanize_bytes_local(2048).endswith("K")
