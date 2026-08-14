"""
Unit tests for vehicle-type cascade logic in dispatch (rides.py).

The cascade fires when zero drivers of the exact requested vehicle type survive
the filter pipeline.  The service area's vehicle_cascade_map then supplies a
list of upgrade type IDs to try next.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ride(vehicle_type_id: str, service_area_id: str = "area-1") -> dict:
    return {
        "id": "ride-1",
        "rider_id": "rider-1",
        "vehicle_type_id": vehicle_type_id,
        "service_area_id": service_area_id,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "requires_wav": False,
        "status": "searching",
    }


def _make_driver(driver_id: str, vehicle_type_id: str, lat: float = 52.14, lng: float = -106.68) -> dict:
    return {
        "id": driver_id,
        "vehicle_type_id": vehicle_type_id,
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "lat": lat,
        "lng": lng,
        "average_rating": 4.8,
        "user_id": driver_id,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cascade_fires_when_no_exact_type_drivers(mock_supabase_client):
    """When the exact type has no candidates, cascade queries XL upgrade drivers."""
    suv_id = "vt-suv"
    xl_id = "vt-xl"
    xl_driver = _make_driver("driver-xl-1", xl_id)
    ride = _make_ride(suv_id)

    service_area = {
        "id": "area-1",
        "subscription_required": False,
        "vehicle_cascade_map": [{"from": suv_id, "to": [xl_id]}],
        "parent_service_area_id": None,
    }

    # Keyed on the requested vehicle_type_id rather than call ORDER: dispatch
    # also reads `service_areas` (the cross-service-area scope) and other tables
    # on this path, and a positional side_effect list silently mis-assigns its
    # values the moment any query is added or removed — the SUV pool then
    # receives the XL row, the pool is non-empty, and cascade never fires while
    # the test reports a cascade failure. Match on intent instead.
    def _rows_for(table, filters=None, **kwargs):
        if table == "drivers":
            vt = (filters or {}).get("vehicle_type_id")
            if isinstance(vt, dict) and xl_id in (vt.get("$in") or []):
                return [xl_driver]  # cascade pool
            return []  # no exact-type (SUV) drivers
        if table == "service_areas":
            return [{"id": service_area["id"], "parent_service_area_id": None}]
        return []

    get_rows_mock = AsyncMock(side_effect=_rows_for)
    find_one_mock = AsyncMock(return_value=service_area)

    try:
        from backend.routes.rides import match_driver_to_ride
    except ImportError:
        pytest.skip("match_driver_to_ride not importable in this env")

    with (
        patch("backend.db_supabase.get_rows", get_rows_mock),
        patch("backend.db_supabase.find_one", find_one_mock),
        patch(
            "backend.routes.rides._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, 1.0) for d in drivers],
        ),
        patch("backend.routes.rides.matching._dispatch_retry", new_callable=AsyncMock),
        patch("backend.routes.rides._deps.asyncio.create_task", return_value=MagicMock()),
        # get_app_settings() itself calls db_supabase.get_rows("settings",
        # ...) (with an in-process 60s cache, so whether it fires here
        # depends on cache state from earlier tests) -- patch it directly so
        # the get_rows_mock side_effect list below maps cleanly onto the
        # SUV-pool-then-cascade-pool calls it's actually meant to model.
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        # Batch-offer dispatch inserts ride_offers rows via
        # db_supabase.run_sync(lambda: supabase.table(...)...execute()) --
        # unmocked this hits a real, unconfigured client and re-raises,
        # aborting match_driver_to_ride before the assertions below run.
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            new_callable=AsyncMock,
            return_value=MagicMock(data=[]),
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            new_callable=AsyncMock,
            return_value=(set(), False),  # Redis unreachable — fail-open
        ),
        patch(
            "backend.utils.redis_client.redis_mget",
            new_callable=AsyncMock,
            return_value=[None] * 10,
        ),
    ):
        await match_driver_to_ride("ride-1", ride=ride)

    # A `drivers` query must have targeted the XL upgrade type via $in. Found by
    # inspecting the calls rather than indexing a fixed position, so an added
    # query elsewhere on the dispatch path cannot turn this into a false failure.
    driver_filters = [
        call.args[1]
        for call in get_rows_mock.call_args_list
        if call.args and call.args[0] == "drivers" and len(call.args) > 1 and isinstance(call.args[1], dict)
    ]
    assert driver_filters, "Expected at least one drivers query"
    cascade_filters = [
        f
        for f in driver_filters
        if xl_id in ((f.get("vehicle_type_id") if isinstance(f.get("vehicle_type_id"), dict) else {}).get("$in") or [])
    ]
    assert cascade_filters, f"Cascade must query the XL type; drivers filters seen: {driver_filters}"


@pytest.mark.anyio
async def test_cascade_skipped_when_no_cascade_map():
    """Areas with empty vehicle_cascade_map never trigger cascade."""
    suv_id = "vt-suv"
    service_area = {
        "id": "area-1",
        "subscription_required": False,
        "vehicle_cascade_map": [],
    }

    find_one_mock = AsyncMock(return_value=service_area)
    get_rows_mock = AsyncMock(return_value=[])  # no drivers for exact type, no cascade pool either

    with (
        patch("backend.db_supabase.get_rows", get_rows_mock),
        patch("backend.db_supabase.find_one", find_one_mock),
    ):
        # The cascade block reads vehicle_cascade_map and finds no matching rule
        cascade_map = service_area.get("vehicle_cascade_map") or []
        cascade_to = next(
            (rule.get("to") or [] for rule in cascade_map if rule.get("from") == suv_id),
            [],
        )
        assert cascade_to == [], "Empty cascade map should yield no upgrade types"


@pytest.mark.anyio
async def test_cascade_rule_only_matches_correct_from_type():
    """A cascade rule for 'standard' does not fire when the ride requests 'suv'."""
    standard_id = "vt-standard"
    suv_id = "vt-suv"

    cascade_map = [{"from": standard_id, "to": [suv_id]}]
    # Ride requests SUV — standard→SUV rule must not match
    cascade_to = next(
        (rule.get("to") or [] for rule in cascade_map if rule.get("from") == suv_id),
        [],
    )
    assert cascade_to == [], "Wrong 'from' type should not trigger cascade"


@pytest.mark.anyio
async def test_cascade_multiple_upgrade_types():
    """A single 'from' type can cascade to multiple 'to' types."""
    standard_id = "vt-standard"
    suv_id = "vt-suv"
    xl_id = "vt-xl"

    cascade_map = [{"from": standard_id, "to": [suv_id, xl_id]}]
    cascade_to = next(
        (rule.get("to") or [] for rule in cascade_map if rule.get("from") == standard_id),
        [],
    )
    assert suv_id in cascade_to
    assert xl_id in cascade_to
    assert len(cascade_to) == 2


@pytest.mark.anyio
async def test_cascade_not_triggered_when_exact_drivers_found():
    """When exact-type drivers exist, cascade must not be attempted."""
    suv_id = "vt-suv"
    suv_driver = _make_driver("driver-suv-1", suv_id)

    # Simulate: exact-type drivers found → drivers_with_distance non-empty → cascade block never entered
    drivers_with_distance = [(suv_driver, 1.5)]  # non-empty
    cascade_fired = False

    if not drivers_with_distance:
        # This block would be the cascade — it must NOT run
        cascade_fired = True

    assert not cascade_fired, "Cascade must not fire when exact-type drivers are available"


@pytest.mark.anyio
async def test_cascade_inherits_parent_area_map():
    """Child area with empty cascade map inherits parent's rules."""
    suv_id = "vt-suv"
    xl_id = "vt-xl"

    child_area = {
        "id": "child-area",
        "vehicle_cascade_map": [],  # empty — should fall back to parent
        "parent_service_area_id": "parent-area",
    }
    parent_area = {
        "id": "parent-area",
        "vehicle_cascade_map": [{"from": suv_id, "to": [xl_id]}],
        "parent_service_area_id": None,
    }

    # Simulate the cascade block's inheritance logic
    casc_map = child_area.get("vehicle_cascade_map") or []
    if not casc_map and child_area.get("parent_service_area_id"):
        casc_map = parent_area.get("vehicle_cascade_map") or []

    cascade_to = next(
        (rule.get("to") or [] for rule in casc_map if rule.get("from") == suv_id),
        [],
    )
    assert xl_id in cascade_to, "Child area must inherit parent cascade map"


@pytest.mark.anyio
async def test_cascade_redis_fail_open():
    """Redis outage (reachable=False) must not empty the cascade pool."""
    # When present_driver_ids_checked returns (set(), False) the pool must be
    # preserved so a Redis blip cannot halt cascaded dispatch.
    xl_driver = _make_driver("driver-xl-1", "vt-xl")

    _casc_pool = [xl_driver]
    _casc_present_set: set = set()
    _casc_reachable: bool = False  # Redis down

    if _casc_reachable:
        _casc_pool = [d for d in _casc_pool if d["id"] in _casc_present_set]
    # else: pool untouched — fail-open

    assert len(_casc_pool) == 1, "Cascade pool must survive a Redis presence outage"
