"""Tests for the finalizer's stats-only distance recompute on late GPS tails.

Contract under test (_recompute_ride_distance_stats):
  - only completed rides are touched;
  - a delta <= DISTANCE_RECOMPUTE_EPSILON_KM is a no-op (replay safety);
  - stats columns + ride_metrics actuals are updated; fare columns never;
  - distance_km is mirrored ONLY under fare-lock (display-only there);
  - every applied change writes an append-only ride_distance_recomputes row;
  - a lost CAS (ride row not matched) writes no audit row.
"""

import asyncio
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.utils.route_finalizer import (
    DISTANCE_RECOMPUTE_EPSILON_KM,
    _recompute_ride_distance_stats,
)
from backend.utils.trip_distance import TripDistanceResult


def _ride(**over):
    ride = {
        "id": "ride_1",
        "status": "completed",
        "planned_distance_km": 3.0,
        "distance_km": 3.0,
        "actual_distance_km": 7.2,
        "phase_distances": {"trip_in_progress": 7.2},
        "total_fare": "23.91",
        "ride_metrics": {
            "phases": {
                "navigating_to_pickup": {"estimated_distance_km": 1.5, "actual_distance_km": 1.4},
                "trip_in_progress": {"estimated_distance_km": 3.0, "actual_distance_km": 7.2},
            }
        },
    }
    ride.update(over)
    return ride


def _distances(actual=9.9, pickup=1.6):
    return TripDistanceResult(
        actual_distance_km=actual,
        pickup_to_driver_km=pickup,
        phase_distances={"trip_in_progress": actual, "navigating_to_pickup": pickup},
        phase_durations={"trip_in_progress": 1500},
        gps_points_count=240,
        trip_points_count=200,
        actual_distance_km_haversine=actual,
        route_quality={"confidence": "high"},
    )


def _run(coro):
    return asyncio.run(coro)


@contextmanager
def _ctx(update_result, *, fare_lock=False, distances=None):
    mocks = SimpleNamespace(
        load=AsyncMock(return_value=[]),
        compute=AsyncMock(return_value=distances or _distances()),
        update=AsyncMock(return_value=update_result),
        insert=AsyncMock(return_value={"id": "audit_1"}),
        settings=AsyncMock(return_value={"fare_lock_enabled": fare_lock}),
    )
    with ExitStack() as stack:
        stack.enter_context(patch("backend.utils.route_finalizer.load_ride_breadcrumbs", mocks.load))
        stack.enter_context(patch("backend.utils.route_finalizer.compute_trip_distances", mocks.compute))
        stack.enter_context(patch("backend.utils.route_finalizer.db_supabase.update_one", mocks.update))
        stack.enter_context(patch("backend.utils.route_finalizer.db_supabase.insert_one", mocks.insert))
        # The finalizer binds get_app_settings into its own module namespace
        # (`from ..settings_loader import get_app_settings`), so patch it there.
        stack.enter_context(patch("backend.utils.route_finalizer.get_app_settings", mocks.settings))
        yield mocks


def test_recompute_updates_stats_and_writes_audit_never_fare():
    with _ctx({"id": "ride_1"}) as mocks:
        _run(_recompute_ride_distance_stats("ride_1", _ride(), revision=2))

    args, kwargs = mocks.update.await_args
    assert args[0] == "rides"
    assert args[1] == {"id": "ride_1", "status": "completed"}
    fields = args[2]
    assert fields["actual_distance_km"] == 9.9
    assert fields["pickup_to_driver_km"] == 1.6
    assert fields["gps_points_count"] == 240
    assert fields["ride_metrics"]["phases"]["trip_in_progress"]["actual_distance_km"] == 9.9
    # Estimates in ride_metrics are preserved, only actuals overwritten.
    assert fields["ride_metrics"]["phases"]["trip_in_progress"]["estimated_distance_km"] == 3.0
    # Fare columns must never appear in a stats recompute.
    forbidden = {"total_fare", "distance_fare", "time_fare", "driver_earnings", "fare_breakdown_snapshot"}
    assert forbidden.isdisjoint(fields)
    # No fare-lock: distance_km fed the settled fare, must not drift.
    assert "distance_km" not in fields
    assert kwargs["upsert"] is False

    audit_args, _ = mocks.insert.await_args
    assert audit_args[0] == "ride_distance_recomputes"
    audit = audit_args[1]
    assert audit["previous_actual_distance_km"] == 7.2
    assert audit["new_actual_distance_km"] == 9.9
    assert audit["route_revision"] == 2
    assert audit["trigger"] == "late_tail_refinalization"


def test_fare_lock_mirrors_display_only_distance_km():
    with _ctx({"id": "ride_1"}, fare_lock=True) as mocks:
        _run(_recompute_ride_distance_stats("ride_1", _ride(), revision=3))

    fields = mocks.update.await_args[0][2]
    assert fields["distance_km"] == 9.9


def test_delta_inside_epsilon_is_a_noop():
    small = _distances(actual=7.2 + DISTANCE_RECOMPUTE_EPSILON_KM)
    with _ctx({"id": "ride_1"}, distances=small) as mocks:
        _run(_recompute_ride_distance_stats("ride_1", _ride(), revision=4))

    mocks.update.assert_not_awaited()
    mocks.insert.assert_not_awaited()


def test_non_completed_ride_is_never_touched():
    with _ctx({"id": "ride_1"}) as mocks:
        _run(_recompute_ride_distance_stats("ride_1", _ride(status="in_progress"), revision=2))

    mocks.compute.assert_not_awaited()
    mocks.update.assert_not_awaited()
    mocks.insert.assert_not_awaited()


def test_lost_cas_writes_no_audit_row():
    with _ctx(None) as mocks:
        _run(_recompute_ride_distance_stats("ride_1", _ride(), revision=2))

    mocks.update.assert_awaited_once()
    mocks.insert.assert_not_awaited()
