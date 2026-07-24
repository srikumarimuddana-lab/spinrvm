"""Golden-trace tests for utils/trip_distance.py.

compute_trip_distances was extracted verbatim from complete_ride so the route
finalizer can recompute measured distance when a late tail batch arrives.
These tests pin the historical behaviour: spike filter caps, sparse-GPS
fallback, road-snap sanity band, per-phase totals, and route_quality shape.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.utils.trip_distance import (
    MAX_BREADCRUMBS,
    TripDistanceResult,
    compute_trip_distances,
    load_ride_breadcrumbs,
)

_T0 = datetime(2026, 7, 19, 9, 46, 0, tzinfo=timezone.utc)


def _crumb(i, lat, lng, *, phase="trip_in_progress", dt_s=None):
    ts = (_T0 + timedelta(seconds=dt_s if dt_s is not None else i * 10)).isoformat()
    return {"lat": lat, "lng": lng, "timestamp": ts, "tracking_phase": phase}


def _straight_trace(n, *, phase="trip_in_progress", step_lat=0.001, dt_step=10):
    """n points heading north; 0.001 deg lat ~ 111 m per step."""
    return [
        _crumb(i, 50.40 + i * step_lat, -104.62, phase=phase, dt_s=i * dt_step)
        for i in range(n)
    ]


def _run(coro):
    return asyncio.run(coro)


def _no_road_snap():
    return patch(
        "backend.utils.route_distance.compute_road_route",
        side_effect=RuntimeError("provider down"),
    )


def test_empty_trace_returns_planned_distance_and_low_confidence():
    with _no_road_snap():
        result = _run(compute_trip_distances([], ride_id="r1", planned_distance=7.3))
    assert isinstance(result, TripDistanceResult)
    assert result.actual_distance_km == 7.3
    assert result.gps_points_count == 0
    assert result.route_quality == {"confidence": "low", "reason": "no_gps_breadcrumbs"}


def test_phase_totals_and_billable_portion():
    trace = (
        _straight_trace(10, phase="navigating_to_pickup")
        + [
            _crumb(i, 50.409 + (i - 9) * 0.001, -104.62, phase="trip_in_progress", dt_s=i * 10)
            for i in range(10, 40)
        ]
    )
    with _no_road_snap():
        result = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=2.0))

    # ~111m per step: nav = 9 segments, trip = 30 segments (incl. transition).
    assert 0.9 <= result.phase_distances["navigating_to_pickup"] <= 1.1
    assert 3.0 <= result.phase_distances["trip_in_progress"] <= 3.7
    assert result.actual_distance_km == round(result.phase_distances["trip_in_progress"], 2)
    assert result.pickup_to_driver_km == round(result.phase_distances["navigating_to_pickup"], 2)
    assert result.trip_points_count == 30
    assert result.phase_durations["trip_in_progress"] == 300
    assert result.route_quality["confidence"] == "high"
    assert result.route_quality["distance_provider"] == "haversine_filtered"


def test_teleport_spike_is_rejected_not_summed():
    trace = _straight_trace(20)
    # Inject a 100 km teleport mid-trace (tower handoff).
    trace[10] = _crumb(10, 51.5, -104.62, dt_s=100)
    with _no_road_snap():
        result = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=2.0))
    # Both segments touching the spike exceed MAX_SEG_KM and are dropped.
    assert result.rejected_segments == 2
    assert result.actual_distance_km < 5.0


def test_sparse_gps_falls_back_to_planned_distance():
    trace = _straight_trace(4)  # < 5 trip points
    with _no_road_snap():
        result = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=9.9))
    assert result.actual_distance_km == 9.9
    assert result.route_quality["confidence"] == "low"


def test_all_segments_rejected_falls_back_to_planned_distance():
    # >= 5 trip points but every consecutive pair exceeds MAX_SEG_KM.
    trace = [_crumb(i, 50.0 + i * 0.1, -104.62, dt_s=i * 10) for i in range(8)]
    with _no_road_snap():
        result = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=6.6))
    assert result.actual_distance_km == 6.6
    assert result.rejected_segments == 7


def test_road_snap_wins_inside_sanity_band_and_keeps_polyline():
    trace = _straight_trace(30)

    async def _road(_breadcrumbs):
        return {"distance_km": 3.61, "provider": "osrm_match", "polyline": [[50.4, -104.62]]}

    with patch("backend.utils.route_distance.compute_road_route", _road):
        result = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=2.0))

    assert result.actual_distance_km == 3.61
    assert result.road_distance_provider == "osrm_match"
    assert result.road_polyline == [[50.4, -104.62]]
    assert result.route_quality["road_snap_accepted"] is True
    assert result.actual_distance_km_haversine is not None


def test_road_snap_outside_sanity_band_keeps_haversine():
    trace = _straight_trace(30)

    async def _road(_breadcrumbs):
        return {"distance_km": 400.0, "provider": "osrm_match", "polyline": [[50.4, -104.62]]}

    with patch("backend.utils.route_distance.compute_road_route", _road):
        result = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=2.0))

    assert result.actual_distance_km == result.actual_distance_km_haversine
    assert result.road_distance_provider == "haversine_filtered"
    assert result.road_polyline == []
    assert result.route_quality["road_snap_accepted"] is False


def test_road_snap_timeout_keeps_haversine():
    trace = _straight_trace(30)

    async def _hang(_breadcrumbs):
        await asyncio.sleep(30)

    with patch("backend.utils.route_distance.compute_road_route", _hang):
        result = _run(
            compute_trip_distances(
                trace, ride_id="r1", planned_distance=2.0, road_snap_timeout_s=0.05
            )
        )
    assert result.actual_distance_km == result.actual_distance_km_haversine
    assert result.road_distance_provider == "haversine_filtered"


def test_phase_polylines_are_downsampled_and_keep_endpoints():
    trace = _straight_trace(600)
    with _no_road_snap():
        result = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=2.0))
    poly = result.phase_polylines["trip_in_progress"]
    assert len(poly) <= 151
    assert poly[0][0] == round(trace[0]["lat"], 6)
    assert poly[-1][0] == round(trace[-1]["lat"], 6)


def test_load_ride_breadcrumbs_pages_filters_and_sorts():
    pages = {
        0: [
            {"lat": 50.41, "lng": -104.62, "timestamp": "2026-07-19T09:47:00+00:00"},
            {"lat": None, "lng": -104.62, "timestamp": "2026-07-19T09:47:10+00:00"},
            {"lat": 50.40, "lng": -104.62, "timestamp": "2026-07-19T09:46:00+00:00"},
        ]
    }

    async def _get_rows(table, query, *, order, limit, offset):
        assert table == "driver_location_history"
        assert query == {"ride_id": "r1"}
        assert order == "timestamp"
        return pages.get(offset, [])

    with patch("backend.utils.trip_distance.db_supabase.get_rows", _get_rows):
        crumbs = _run(load_ride_breadcrumbs("r1"))

    assert len(crumbs) == 2, "null-coordinate rows filtered"
    assert crumbs[0]["timestamp"] < crumbs[1]["timestamp"], "sorted by timestamp"
    assert MAX_BREADCRUMBS == 10000


# --- GPS filter_mode (P1.2) ---------------------------------------------------

def _crumb_as(dt_s, lat, lng, *, phase="trip_in_progress", accuracy=8.0, speed=0.0):
    ts = (_T0 + timedelta(seconds=dt_s)).isoformat()
    return {
        "lat": lat,
        "lng": lng,
        "timestamp": ts,
        "tracking_phase": phase,
        "accuracy": accuracy,
        "speed": speed,
    }


def _clean_then_jitter():
    """6 clean moving fixes, then a 6-fix stationary jitter cluster ~111 m on.

    The jitter is well-formed (accuracy 8 m, speed 0) so only the stationary
    collapse — not the accuracy gate — removes it.
    """
    trace = [
        _crumb_as(i * 4, 50.400 + i * 0.001, -104.62, accuracy=8.0, speed=15.0)
        for i in range(6)
    ]  # ~555 m of real northward travel
    base_lat, base_lng = 50.406, -104.62
    offsets = [(0.0, 0.0), (0.00006, 0.00003), (-0.00005, 0.00004),
               (0.00003, -0.00006), (-0.00004, -0.00002), (0.00002, 0.00005)]
    for k, (dlat, dlng) in enumerate(offsets):
        trace.append(_crumb_as(24 + k * 4, base_lat + dlat, base_lng + dlng, accuracy=8.0, speed=0.0))
    return trace


def test_off_mode_is_legacy_no_filter_stats():
    trace = _clean_then_jitter()
    with _no_road_snap():
        result = _run(compute_trip_distances(trace, ride_id="r_off", planned_distance=1.0, filter_mode="off"))
    assert "filter_mode" not in result.route_quality
    assert "collapsed_stationary" not in result.route_quality


def test_shadow_mode_bills_raw_and_reports_delta():
    trace = _clean_then_jitter()
    with _no_road_snap():
        off = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=1.0, filter_mode="off"))
        shadow = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=1.0, filter_mode="shadow"))
    # Shadow must not change the billed distance — same as legacy/off.
    assert shadow.actual_distance_km == off.actual_distance_km
    # But it discloses the filtering effect.
    assert shadow.route_quality["filter_mode"] == "shadow"
    assert shadow.route_quality["collapsed_stationary"] == 4  # 6 jitter fixes → 2
    assert shadow.route_quality["dropped_low_accuracy"] == 0
    assert shadow.route_quality["filtered_delta_km"] > 0
    assert shadow.route_quality["actual_distance_km_filtered"] < off.actual_distance_km


def test_on_mode_bills_filtered_and_keeps_raw_reference():
    trace = _clean_then_jitter()
    with _no_road_snap():
        off = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=1.0, filter_mode="off"))
        on = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=1.0, filter_mode="on"))
    # Filtered distance is lower than the jitter-inflated raw value...
    assert on.actual_distance_km < off.actual_distance_km
    # ...and the raw value is preserved for reference/audit.
    assert on.route_quality["actual_distance_km_unfiltered"] == off.actual_distance_km
    assert on.route_quality["collapsed_stationary"] == 4


def test_on_mode_drops_low_accuracy_fixes():
    # A clean straight trace with two garbage-accuracy fixes spliced in.
    trace = [_crumb_as(i * 4, 50.400 + i * 0.001, -104.62, accuracy=8.0, speed=15.0) for i in range(6)]
    trace.insert(3, _crumb_as(10, 50.500, -104.70, accuracy=90.0, speed=15.0))  # far, low-accuracy
    trace.insert(5, _crumb_as(18, 50.300, -104.50, accuracy=120.0, speed=15.0))
    with _no_road_snap():
        on = _run(compute_trip_distances(trace, ride_id="r1", planned_distance=1.0, filter_mode="on"))
    assert on.route_quality["dropped_low_accuracy"] == 2
