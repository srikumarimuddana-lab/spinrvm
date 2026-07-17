"""Regression tests for the PRIMARY dispatch path's presence filter (C2).

A Redis outage must fail OPEN: `present_driver_ids_checked` returning
``(set(), reachable=False)`` means the presence store could not be consulted,
NOT that every driver is offline. The old code gated on a liveness probe of
the lazily-created client (which looks live mid-outage) and then applied the
unchecked wrapper's empty set — emptying the candidate pool fleet-wide until
the stuck-ride sweeper cancelled every searching ride.

The cascade branch already handled this correctly (test_dispatch_cascade.py);
these tests pin the same contract on the primary path.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import metrics


def _counter_total(name: str) -> int:
    return sum(metrics.snapshot()["counters"].get(name, {}).values())


def _make_ride() -> dict:
    return {
        "id": "ride-1",
        "rider_id": "rider-1",
        "vehicle_type_id": "vt-standard",
        "service_area_id": "area-1",
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "requires_wav": False,
        "status": "searching",
    }


def _make_driver(driver_id: str) -> dict:
    return {
        "id": driver_id,
        "user_id": driver_id,
        "vehicle_type_id": "vt-standard",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "lat": 52.14,
        "lng": -106.68,
        "rating": 4.8,
        "average_rating": 4.8,
    }


async def _run_match(presence_result, drivers, ranked_pools):
    """Drive match_driver_to_ride with a stubbed presence store.

    ``ranked_pools`` collects the candidate list that survives the presence
    filter and reaches filter_and_rank_drivers.
    """
    ride = _make_ride()
    service_area = {
        "id": "area-1",
        "subscription_required": False,
        "vehicle_cascade_map": [],
        "parent_service_area_id": None,
    }

    try:
        from backend.routes.rides import match_driver_to_ride
    except ImportError:
        pytest.skip("match_driver_to_ride not importable in this env")

    def _record_rank(ride_arg, pool, *a, **kw):
        ranked_pools.append(list(pool))
        return [(d, 1.0) for d in pool]

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=drivers)),
        patch("backend.db_supabase.find_one", AsyncMock(return_value=service_area)),
        patch("backend.routes.rides._deps.filter_and_rank_drivers", side_effect=_record_rank),
        patch("backend.routes.rides.matching._dispatch_retry", new_callable=AsyncMock),
        patch("backend.routes.rides._deps.asyncio.create_task", return_value=MagicMock()),
        patch("backend.routes.rides._send_offer_to_driver", new_callable=AsyncMock),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            new_callable=AsyncMock,
            return_value=presence_result,
        ),
        patch(
            "backend.utils.redis_client.redis_mget",
            new_callable=AsyncMock,
            return_value=[None] * max(len(drivers), 1),
        ),
    ):
        await match_driver_to_ride("ride-1", ride=ride)


@pytest.mark.anyio
async def test_redis_unreachable_does_not_empty_pool(mock_supabase_client):
    """reachable=False → presence filter skipped; all DB-online drivers survive."""
    drivers = [_make_driver("driver-1"), _make_driver("driver-2")]
    ranked_pools: list = []
    before = _counter_total("spinr_dispatch_presence_filter_failed_total")

    await _run_match((set(), False), drivers, ranked_pools)

    assert ranked_pools, "ranking never ran — dispatch aborted before offer stage"
    surviving = {d["id"] for d in ranked_pools[0]}
    assert surviving == {"driver-1", "driver-2"}, f"Redis outage emptied the candidate pool (fail-closed): {surviving}"
    assert _counter_total("spinr_dispatch_presence_filter_failed_total") == before + 1, (
        "degraded presence path must emit spinr_dispatch_presence_filter_failed_total"
    )


@pytest.mark.anyio
async def test_reachable_empty_set_fails_open(mock_supabase_client):
    """reachable=True with an empty set is ambiguous (all-ghost / bootstrap /
    dev in-process dict). The primary path fails open and keeps DB-online
    drivers — same as DispatchService.find_candidate_drivers — rather than
    report a false "no drivers". The accept-time atomic claim is the ghost
    backstop. This also preserves the many primary-path tests that exercise
    match_driver_to_ride without seeding presence."""
    drivers = [_make_driver("driver-1")]
    ranked_pools: list = []
    before = _counter_total("spinr_dispatch_presence_filter_failed_total")

    await _run_match((set(), True), drivers, ranked_pools)

    assert ranked_pools, "ranking never ran — dispatch aborted before offer stage"
    assert {d["id"] for d in ranked_pools[0]} == {"driver-1"}, (
        "reachable-but-empty presence must not empty the pool (fail open)"
    )
    # An empty-but-reachable set is not a degradation — no metric bump.
    assert _counter_total("spinr_dispatch_presence_filter_failed_total") == before


@pytest.mark.anyio
async def test_reachable_partial_set_filters_to_present(mock_supabase_client):
    """reachable=True keeps exactly the drivers whose heartbeat is alive."""
    drivers = [_make_driver("driver-live"), _make_driver("driver-ghost")]
    ranked_pools: list = []

    await _run_match(({"driver-live"}, True), drivers, ranked_pools)

    assert ranked_pools, "ranking never ran — dispatch aborted before offer stage"
    assert {d["id"] for d in ranked_pools[0]} == {"driver-live"}
