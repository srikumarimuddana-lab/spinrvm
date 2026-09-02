"""Dispatch candidate providers: default legacy, H3 failover is loud."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.dispatch_candidates import (
    DEFAULT_PROVIDER,
    fetch_dispatch_candidates,
    resolve_provider,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

FILTER = {
    "is_online": True,
    "is_available": True,
    "is_verified": True,
    "status": "active",
    "$and": [{"lat": {"$gte": 1}}, {"lat": {"$lte": 2}}],
}
LEGACY_ROWS = [{"id": "a", "lat": 52.13, "lng": -106.67}]


def test_resolve_provider_defaults_legacy():
    assert resolve_provider({}, None) == DEFAULT_PROVIDER
    assert resolve_provider({"dispatch_geo_provider": "nope"}, None) == DEFAULT_PROVIDER


def test_area_override_beats_global():
    assert resolve_provider({"dispatch_geo_provider": "h3"}, {"dispatch_geo_provider": "legacy"}) == "legacy"
    assert resolve_provider({"dispatch_geo_provider": "h3"}, {"dispatch_geo_provider": ""}) == "h3"


async def test_legacy_calls_get_rows_with_the_box_filter():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=LEGACY_ROWS)
    with patch("services.dispatch_candidates.remember_last_served", AsyncMock()) as remember:
        rows = await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "legacy"},
            area=None,
            ride_id="ride-1",
        )
    assert rows == LEGACY_ROWS
    db.get_rows.assert_awaited_once()
    assert db.get_rows.await_args.args[1]["$and"] == FILTER["$and"]
    remember.assert_not_awaited()


async def test_shadow_records_legacy_as_served_provider():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=LEGACY_ROWS)

    def capture(coro):
        coro.close()
        return None

    with (
        patch("services.dispatch_candidates.spawn", side_effect=capture),
        patch("services.dispatch_candidates.remember_last_served", AsyncMock()) as remember,
        patch("services.dispatch_candidates.get_last_served", AsyncMock(return_value=None)),
        patch("services.dispatch_candidates.recent_events", AsyncMock(return_value=[])),
    ):
        rows = await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "shadow"},
            area=None,
            ride_id="ride-shadow",
        )
    assert rows == LEGACY_ROWS
    remember.assert_awaited_once()
    kwargs = remember.await_args.kwargs
    assert kwargs["configured"] == "shadow"
    assert kwargs["provider"] == "legacy"
    assert kwargs["failed_over"] is False


async def test_h3_unready_fails_over_to_postgis_then_legacy():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=LEGACY_ROWS)
    db.rpc = AsyncMock(side_effect=RuntimeError("rpc missing"))
    db.get_rows_batched_in = AsyncMock(return_value=[])

    with (
        patch("services.dispatch_candidates.is_ready", AsyncMock(return_value=False)),
        patch("services.dispatch_candidates._notify_admin_failover", AsyncMock()) as notify,
        patch("services.dispatch_candidates.record_event", AsyncMock()) as ev,
    ):
        rows = await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "h3"},
            area=None,
            ride_id="ride-9",
        )
    assert rows == LEGACY_ROWS
    # PostGIS raised → legacy box query.
    db.get_rows.assert_awaited()
    assert notify.await_count >= 1
    assert ev.await_count >= 1
    failovers = [c.args[0] for c in notify.await_args_list]
    assert any(c.get("from_provider") == "h3" for c in failovers)


async def test_h3_ready_empty_ring_is_not_a_failover():
    """Healthy empty market must not look like an outage."""
    db = MagicMock()
    db.get_rows = AsyncMock()
    db.get_rows_batched_in = AsyncMock(return_value=[])
    with (
        patch("services.dispatch_candidates.is_ready", AsyncMock(return_value=True)),
        patch("services.dispatch_candidates.query_driver_ids", AsyncMock(return_value=set())),
        patch("services.dispatch_candidates._notify_admin_failover", AsyncMock()) as notify,
    ):
        rows = await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "h3"},
            area=None,
        )
    assert rows == []
    notify.assert_not_awaited()
    db.get_rows.assert_not_awaited()


async def test_shadow_serves_legacy_even_when_h3_diverges():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=LEGACY_ROWS)
    db.get_rows_batched_in = AsyncMock(return_value=[{"id": "b", "lat": 52.13, "lng": -106.67}])
    tasks: list = []

    def capture(coro):
        task = asyncio.get_event_loop().create_task(coro)
        tasks.append(task)
        return task

    with (
        patch("services.dispatch_candidates.is_ready", AsyncMock(return_value=True)),
        patch("services.dispatch_candidates.query_driver_ids", AsyncMock(return_value={"b"})),
        patch("services.dispatch_candidates.spawn", side_effect=capture),
        patch("services.dispatch_candidates.record_event", AsyncMock()) as ev,
        patch("services.dispatch_candidates.notify_dispatch_geo_ops", AsyncMock()),
    ):
        rows = await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "shadow"},
            area=None,
            ride_id="ride-3",
        )
        await asyncio.gather(*tasks)
    assert rows == LEGACY_ROWS
    assert any(c.args[0] == "shadow_diverge" for c in ev.await_args_list)


async def test_h3_failover_records_last_served_for_admin_banner():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=LEGACY_ROWS)
    db.rpc = AsyncMock(side_effect=RuntimeError("rpc missing"))
    db.get_rows_batched_in = AsyncMock(return_value=[])

    with (
        patch("services.dispatch_candidates.is_ready", AsyncMock(return_value=False)),
        patch("services.dispatch_candidates._notify_admin_failover", AsyncMock()),
        patch("services.dispatch_candidates.record_event", AsyncMock()),
        patch("services.dispatch_candidates.remember_last_served", AsyncMock()) as remember,
        patch("services.dispatch_candidates.get_last_served", AsyncMock(return_value=None)),
        patch("services.dispatch_candidates.recent_events", AsyncMock(return_value=[])),
    ):
        await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "h3"},
            area=None,
            ride_id="ride-9",
        )
    remember.assert_awaited()
    kwargs = remember.await_args.kwargs
    assert kwargs["configured"] == "h3"
    assert kwargs["provider"] == "legacy"
    assert kwargs["failed_over"] is True


async def test_postgis_requests_more_ids_than_matching_limit():
    db = MagicMock()
    db.rpc = AsyncMock(return_value=[{"driver_id": "a"}])
    db.get_rows_batched_in = AsyncMock(return_value=LEGACY_ROWS)
    with (
        patch("services.dispatch_candidates.remember_last_served", AsyncMock()),
        patch("services.dispatch_candidates.get_last_served", AsyncMock(return_value=None)),
        patch("services.dispatch_candidates.recent_events", AsyncMock(return_value=[])),
    ):
        await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "postgis"},
            area=None,
            limit=500,
        )
    assert db.rpc.await_args.args[1]["p_limit"] >= 5000


async def test_h3_recovery_notifies_ops():
    db = MagicMock()
    db.get_rows_batched_in = AsyncMock(return_value=LEGACY_ROWS)
    prev = {
        "provider": "legacy",
        "configured": "h3",
        "failed_over": True,
        "reason": "h3_not_ready",
    }
    with (
        patch("services.dispatch_candidates.is_ready", AsyncMock(return_value=True)),
        patch("services.dispatch_candidates.query_driver_ids", AsyncMock(return_value={"a"})),
        patch("services.dispatch_candidates.get_last_served", AsyncMock(return_value=prev)),
        patch("services.dispatch_candidates.remember_last_served", AsyncMock()),
        patch("services.dispatch_candidates.record_event", AsyncMock()) as ev,
        patch("services.dispatch_candidates.notify_dispatch_geo_ops", AsyncMock()) as notify,
    ):
        await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "h3"},
            area=None,
            ride_id="ride-ok",
        )
    assert any(c.args[0] == "recovered" for c in ev.await_args_list)
    assert any(c.args[0].get("kind") == "recovered" for c in notify.await_args_list)


async def test_h3_fetches_all_index_ids_then_keeps_nearest_limit():
    """Slicing the unordered H3 set before the DB read dropped in-radius drivers."""
    captured: dict = {}
    near = {"id": "near", "lat": 52.13, "lng": -106.67}
    far = {"id": "far", "lat": 53.13, "lng": -106.67}

    async def batched(table, column, values, extra_filters=None, columns="*", limit=None, **kw):
        captured["values"] = list(values)
        captured["limit"] = limit
        return [near if v == "near" else {**far, "id": v} for v in values]

    db = MagicMock()
    db.get_rows = AsyncMock()
    db.get_rows_batched_in = AsyncMock(side_effect=batched)
    ids = {f"d{i}" for i in range(40)} | {"near"}
    with (
        patch("services.dispatch_candidates.is_ready", AsyncMock(return_value=True)),
        patch("services.dispatch_candidates.query_driver_ids", AsyncMock(return_value=ids)),
        patch("services.dispatch_candidates._notify_admin_failover", AsyncMock()),
        patch("services.dispatch_candidates.remember_last_served", AsyncMock()),
        patch("services.dispatch_candidates.get_last_served", AsyncMock(return_value=None)),
        patch("services.dispatch_candidates.recent_events", AsyncMock(return_value=[])),
    ):
        rows = await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "h3"},
            area=None,
            limit=10,
        )
    assert len(captured["values"]) == len(ids)
    assert captured["limit"] is None or captured["limit"] >= len(ids)
    assert [r["id"] for r in rows] == ["near"]


async def test_h3_query_failure_reason_is_stable_code_without_coords():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=LEGACY_ROWS)
    db.rpc = AsyncMock(side_effect=RuntimeError("rpc missing"))
    db.get_rows_batched_in = AsyncMock(return_value=[])
    with (
        patch("services.dispatch_candidates.is_ready", AsyncMock(return_value=True)),
        patch(
            "services.dispatch_candidates.query_driver_ids",
            AsyncMock(side_effect=ValueError("lat/lng out of range: 52.13,-106.67")),
        ),
        patch("services.dispatch_candidates._notify_admin_failover", AsyncMock()) as notify,
        patch("services.dispatch_candidates.record_event", AsyncMock()) as ev,
        patch("services.dispatch_candidates.remember_last_served", AsyncMock()),
        patch("services.dispatch_candidates.get_last_served", AsyncMock(return_value=None)),
        patch("services.dispatch_candidates.recent_events", AsyncMock(return_value=[])),
    ):
        rows = await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "h3"},
            area=None,
            ride_id="ride-gps",
        )
    assert rows == LEGACY_ROWS
    reasons = [c.kwargs.get("reason") or (c.args[1] if len(c.args) > 1 else None) for c in ev.await_args_list]
    failover_reasons = [c.args[0].get("reason") for c in notify.await_args_list]
    assert "h3_query_failed" in reasons or "h3_query_failed" in failover_reasons
    blob = " ".join(str(r) for r in reasons + failover_reasons)
    assert "52.13" not in blob
    assert "-106.67" not in blob


async def test_shadow_spawns_compare_off_the_request_path():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=LEGACY_ROWS)
    spawned = []

    def capture(coro):
        spawned.append(coro)
        coro.close()
        return None

    with (
        patch("services.dispatch_candidates.is_ready", AsyncMock(return_value=True)),
        patch("services.dispatch_candidates.query_driver_ids", AsyncMock(return_value={"b"})),
        patch("services.dispatch_candidates.spawn", side_effect=capture) as sp,
    ):
        rows = await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "shadow"},
            area=None,
            ride_id="ride-3",
        )
    assert rows == LEGACY_ROWS
    sp.assert_called_once()
    assert len(spawned) == 1


async def test_shadow_compare_filters_both_sets_to_the_search_circle():
    """Legacy box vs padded H3 disk must not alert on out-of-circle IDs."""
    far_legacy = {"id": "box-only", "lat": 52.13 + 0.2, "lng": -106.67}  # ~22 km
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[far_legacy])
    db.get_rows_batched_in = AsyncMock(return_value=[])
    tasks: list = []

    def capture(coro):
        task = asyncio.get_event_loop().create_task(coro)
        tasks.append(task)
        return task

    with (
        patch("services.dispatch_candidates.is_ready", AsyncMock(return_value=True)),
        patch("services.dispatch_candidates.query_driver_ids", AsyncMock(return_value=set())),
        patch("services.dispatch_candidates.spawn", side_effect=capture),
        patch("services.dispatch_candidates.record_event", AsyncMock()) as ev,
        patch("services.dispatch_candidates.notify_dispatch_geo_ops", AsyncMock()),
    ):
        rows = await fetch_dispatch_candidates(
            db=db,
            dispatch_filter=FILTER,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            search_radius_km=10,
            app_settings={"dispatch_geo_provider": "shadow"},
            area=None,
            ride_id="ride-box",
        )
        await asyncio.gather(*tasks)
    assert rows == [far_legacy]
    assert not any(c.args and c.args[0] == "shadow_diverge" for c in ev.await_args_list)


async def test_admin_status_summary_when_h3_cannot_serve():
    from services.dispatch_candidates import admin_dispatch_geo_status

    with patch(
        "services.dispatch_candidates.health_snapshot",
        AsyncMock(
            return_value={
                "h3_ready": False,
                "blockers": ["redis_not_connected"],
                "events": [],
                "last_served": None,
                "unhealthy": None,
            }
        ),
    ):
        status = await admin_dispatch_geo_status({"dispatch_geo_provider": "h3"})
    assert status["status_summary"]
    assert "fail over" in status["status_summary"].lower()
    assert status["configured_provider"] == "h3"
