"""Unit tests for standalone GPS-geometry settlement (utils/ride_settlement.py).

Contract (settle_completed_ride_geometry):
  - settles only rides that are already `completed` with an assigned driver;
  - replay-safe: an existing ride_routes row means already settled → skip;
  - writes the same artifacts as the driver completion path: ride_routes
    legacy payload, rides geometry-only fields, P2/P3 period-distance audit,
    and the v2 finalizer queue (mark_route_pending);
  - never touches billing: distance_km, fare fields, status, payment_status;
  - never raises into the caller (rider-end / admin / backstop flows).

Regression anchor: ride SPR-PE7TTB — completed via the rider "end ride early"
path with 51 breadcrumbs stored, but gps_points_count=0, no ride_routes row,
no period audit, finalizer never queued.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.utils import ride_settlement as mod

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.run(coro)


_RIDE = {
    "id": "ride-1",
    "status": "completed",
    "driver_id": "driver-1",
    "planned_distance_km": 4.81,
    "assigned_at": "2026-08-17T23:17:56+00:00",
    "driver_accepted_at": "2026-08-17T23:18:00+00:00",
    "ride_started_at": "2026-08-17T23:18:28+00:00",
    "ride_completed_at": "2026-08-17T23:31:55+00:00",
}


def _distances(**overrides):
    base = dict(
        actual_distance_km=4.2,
        phase_distances={"navigating_to_pickup": 0.4, "trip_in_progress": 4.2},
        phase_durations={"trip_in_progress": 780},
        phase_polylines={},
        pickup_to_driver_km=0.4,
        road_polyline=[],
        gps_points_count=51,
        route_quality={"confidence": "medium"},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _wire(monkeypatch, *, ride=_RIDE, existing_route=None, distances=None):
    """Patch every collaborator; return the mocks that record writes."""
    get_rows = AsyncMock(
        side_effect=lambda table, *a, **k: {
            "rides": [ride] if ride else [],
            "ride_routes": existing_route or [],
        }[table]
    )
    update_one = AsyncMock(return_value={"id": "x"})
    monkeypatch.setattr(mod, "db_supabase", SimpleNamespace(get_rows=get_rows, update_one=update_one))
    monkeypatch.setattr(mod, "flush_driver_breadcrumbs", AsyncMock(return_value=0))
    monkeypatch.setattr(mod, "load_ride_breadcrumbs", AsyncMock(return_value=[{"lat": 1, "lng": 2}] * 51))
    monkeypatch.setattr(
        mod, "compute_trip_distances", AsyncMock(return_value=distances or _distances())
    )
    record_periods = AsyncMock(return_value=2)
    monkeypatch.setattr(mod, "record_ride_period_distances", record_periods)
    mark_pending = AsyncMock()
    monkeypatch.setattr(mod, "mark_route_pending", mark_pending)
    monkeypatch.setattr(mod, "_get_gps_distance_filter_mode", AsyncMock(return_value="off"))
    monkeypatch.setattr(mod, "_metric_inc", lambda *a, **k: None)
    return SimpleNamespace(
        get_rows=get_rows, update_one=update_one, record_periods=record_periods, mark_pending=mark_pending
    )


def test_rider_ended_ride_now_settles_geometry(monkeypatch):
    """The SPR-PE7TTB shape: completed ride, breadcrumbs present, no ride_routes row."""
    m = _wire(monkeypatch)

    assert _run(mod.settle_completed_ride_geometry("ride-1", trigger="rider_end")) is True

    # ride_routes legacy payload upsert + rides geometry update, in that order.
    tables = [c.args[0] for c in m.update_one.await_args_list]
    assert tables == ["ride_routes", "rides"]
    route_payload = m.update_one.await_args_list[0].args[2]
    assert route_payload["gps_points_count"] == 51
    assert route_payload["save_status"] == "saved"

    ride_fields = m.update_one.await_args_list[1].args[2]
    assert ride_fields["gps_points_count"] == 51
    assert ride_fields["actual_distance_km"] == 4.2
    assert ride_fields["route_geometry_status"] == "saved"
    # Billing/state fields must never be written by the standalone path.
    for forbidden in ("distance_km", "status", "payment_status", "total_fare", "grand_total"):
        assert forbidden not in ride_fields
    # The update is scoped to still-completed rides.
    assert m.update_one.await_args_list[1].args[1] == {"id": "ride-1", "status": "completed"}

    # P2/P3 audit rows with the assignment-start fallback chain.
    phases = m.record_periods.await_args.kwargs["phases"]
    assert {p["period"] for p in phases} == {2, 3}
    p2 = next(p for p in phases if p["period"] == 2)
    assert p2["started_at"] == _RIDE["assigned_at"]
    assert p2["distance_km"] == 0.4

    # v2 finalizer queued with missing-tail evidence naming the trigger.
    m.mark_pending.assert_awaited_once()
    completion_point = m.mark_pending.await_args.args[1]
    assert completion_point["missing_tail"] is True
    assert completion_point["rejection"] == "no_completion_fix_rider_end"


def test_existing_ride_routes_row_short_circuits(monkeypatch):
    m = _wire(monkeypatch, existing_route=[{"ride_id": "ride-1"}])

    assert _run(mod.settle_completed_ride_geometry("ride-1", trigger="backstop_sweep")) is False
    m.update_one.assert_not_awaited()
    m.record_periods.assert_not_awaited()
    m.mark_pending.assert_not_awaited()


@pytest.mark.parametrize(
    "ride",
    [
        None,
        {**_RIDE, "status": "in_progress"},
        {**_RIDE, "driver_id": None},
    ],
)
def test_ineligible_rides_are_skipped(monkeypatch, ride):
    m = _wire(monkeypatch, ride=ride)

    assert _run(mod.settle_completed_ride_geometry("ride-1", trigger="admin_complete")) is False
    m.update_one.assert_not_awaited()
    m.mark_pending.assert_not_awaited()


def test_aggregation_failure_still_settles_with_defaults(monkeypatch):
    """GPS aggregation raising must not block the audit + finalizer queue."""
    m = _wire(monkeypatch)
    mod.compute_trip_distances.side_effect = RuntimeError("osrm down")

    assert _run(mod.settle_completed_ride_geometry("ride-1", trigger="rider_end")) is True

    ride_fields = m.update_one.await_args_list[1].args[2]
    assert ride_fields["gps_points_count"] == 0
    assert ride_fields["route_quality"]["reason"] == "no_gps_breadcrumbs"
    # Fallback actual distance mirrors the driver path: planned distance.
    assert ride_fields["actual_distance_km"] == _RIDE["planned_distance_km"]
    m.mark_pending.assert_awaited_once()


def test_never_raises_into_caller(monkeypatch):
    m = _wire(monkeypatch)
    m.get_rows.side_effect = RuntimeError("db down")

    assert _run(mod.settle_completed_ride_geometry("ride-1", trigger="rider_end")) is False
