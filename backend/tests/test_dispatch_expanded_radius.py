"""Phase 4 of .claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md —
wider second search pass (migration 469, ``dispatch_expanded_radius_*``).

Drives ``_match_driver_to_ride_attempt`` down its no-drivers path with an area
that has a vehicle cascade, so each run exercises every radius consumer: the
primary geo box, the primary candidate read, the primary
``filter_and_rank_drivers`` gate, and the same three for the cascade pool. Every
test asserts all six saw the same radius — the box and the haversine gate must
never disagree.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio

NORMAL_KM = 10.0


def _ride(seconds_searching: float | None = 120, **kw) -> dict:
    ride = {
        "id": "ride-1",
        "rider_id": "rider-1",
        "vehicle_type_id": "vt-std",
        "service_area_id": "area-1",
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "requires_wav": False,
        "status": "searching",
    }
    if seconds_searching is not None:
        ride["ride_requested_at"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds_searching)).isoformat()
    ride.update(kw)
    return ride


def _settings(enabled=True, after=45, mult=1.5, max_km=20.0) -> dict:
    return {
        "dispatch_expanded_radius_enabled": enabled,
        "dispatch_expanded_radius_after_seconds": after,
        "dispatch_expanded_radius_multiplier": mult,
        "dispatch_expanded_radius_max_km": max_km,
    }


_AREA = {
    "id": "area-1",
    "subscription_required": False,
    "parent_service_area_id": None,
    "vehicle_cascade_map": [{"from": "vt-std", "to": ["vt-xl"]}],
}


async def _run(ride: dict, app_settings: dict, normal_km: float = NORMAL_KM) -> dict:
    """Run one dispatch attempt; return the radius each consumer received."""
    from backend.routes.rides import matching
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    seen: dict = {"geo_bounds": [], "fetch": [], "rank": []}
    real_bounds = matching.dispatch_geo_bounds

    def _bounds(lat, lng, radius, *a, **kw):
        seen["geo_bounds"].append(radius)
        return real_bounds(lat, lng, radius, *a, **kw)

    async def _fetch(**kw):
        seen["fetch"].append(kw["search_radius_km"])
        return []

    def _rank(ride_, drivers, algorithm, min_rating, radius, **kw):
        seen["rank"].append(radius)
        return []

    metric = MagicMock()
    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value=app_settings)),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, normal_km, 3, False, 500)),
        ),
        patch("backend.routes.rides.matching.resolve_dispatch_area_scope", AsyncMock(return_value=(None, True))),
        patch("backend.routes.rides.matching.dispatch_geo_bounds", side_effect=_bounds),
        patch("backend.routes.rides.matching.fetch_dispatch_candidates", side_effect=_fetch),
        patch("backend.routes.rides.matching._deps.filter_and_rank_drivers", side_effect=_rank),
        patch("backend.routes.rides.matching._metric_inc", metric),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])
        mock_db.find_one = AsyncMock(return_value=dict(_AREA))
        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # No drivers anywhere → the normal 10 s retry is scheduled, unchanged.
    mock_retry.assert_called_once_with("ride-1", delay=10, attempt=1)
    # Primary pool + cascade pool both ran, so each consumer saw two calls.
    assert len(seen["geo_bounds"]) == 2, seen
    assert len(seen["fetch"]) == 2, seen
    assert len(seen["rank"]) == 2, seen
    seen["expanded_metric_calls"] = [
        c for c in metric.call_args_list if c.args and c.args[0] == "spinr_dispatch_radius_expanded_total"
    ]
    return seen


def _all_radii(seen: dict) -> set:
    return set(seen["geo_bounds"]) | set(seen["fetch"]) | set(seen["rank"])


@pytest.mark.parametrize(
    "app_settings",
    [
        {},  # migration 469 not applied / columns absent
        _settings(enabled=False, after=0, mult=3.0, max_km=100.0),  # flag off, aggressive values
    ],
)
async def test_flag_off_uses_normal_radius_everywhere(app_settings):
    seen = await _run(_ride(seconds_searching=600), app_settings)
    assert _all_radii(seen) == {NORMAL_KM}
    assert seen["expanded_metric_calls"] == []


async def test_flag_on_before_after_seconds_uses_normal_radius():
    seen = await _run(_ride(seconds_searching=10), _settings(after=45))
    assert _all_radii(seen) == {NORMAL_KM}
    assert seen["expanded_metric_calls"] == []


async def test_flag_on_after_after_seconds_expands_primary_and_cascade():
    seen = await _run(_ride(seconds_searching=120), _settings(after=45, mult=1.5, max_km=20.0))
    # Primary box, primary read, primary rank gate, and the cascade's three —
    # all on the same expanded value.
    assert seen["geo_bounds"] == [15.0, 15.0]
    assert seen["fetch"] == [15.0, 15.0]
    assert seen["rank"] == [15.0, 15.0]
    assert len(seen["expanded_metric_calls"]) == 1


async def test_after_seconds_zero_expands_on_first_attempt():
    seen = await _run(_ride(seconds_searching=0), _settings(after=0, mult=1.5))
    assert _all_radii(seen) == {15.0}


async def test_expanded_radius_capped_at_max_km():
    seen = await _run(_ride(seconds_searching=120), _settings(mult=3.0, max_km=20.0))
    assert _all_radii(seen) == {20.0}


async def test_expanded_radius_never_below_normal_radius():
    # Area radius 25 km is already wider than max_km 20 → keep 25, no expansion.
    seen = await _run(_ride(seconds_searching=120), _settings(mult=1.5, max_km=20.0), normal_km=25.0)
    assert _all_radii(seen) == {25.0}
    assert seen["expanded_metric_calls"] == []


async def test_missing_ride_requested_at_keeps_normal_radius():
    seen = await _run(_ride(seconds_searching=None), _settings(after=0))
    assert _all_radii(seen) == {NORMAL_KM}
    assert seen["expanded_metric_calls"] == []


async def test_scheduled_ride_measures_from_search_start_not_booking():
    # A scheduled ride booked an hour ago whose scheduled→searching flip
    # (which re-stamps ride_requested_at) was 5 s ago has only been searching
    # for 5 s — it must not jump straight to the wide radius.
    booked = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    ride = _ride(seconds_searching=5, created_at=booked, is_scheduled=True, scheduled_time=booked)
    seen = await _run(ride, _settings(after=45))
    assert _all_radii(seen) == {NORMAL_KM}
