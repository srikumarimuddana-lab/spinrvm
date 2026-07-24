"""Tests for pure GPS breadcrumb filtering (utils/gps_filtering.py).

These guard the fix for the incident where a 1.8 km trip rendered as a 2.6 km
jagged loop: low-accuracy fixes must be dropped and near-stationary jitter
clouds collapsed BEFORE distance is summed or the trace is map-matched, while
genuine movement and elapsed time are preserved.
"""

from __future__ import annotations

from geo_utils import calculate_distance
from utils.gps_filtering import (
    MAX_TRUSTED_ACCURACY_M,
    collapse_stationary_clusters,
    filter_low_accuracy,
)

# Anchor somewhere in Regina; ~0.00009 deg latitude ≈ 10 m.
LAT0, LNG0 = 50.4452, -104.6189


def _pt(lat, lng, *, accuracy=8.0, speed=0.0, phase="trip_in_progress", ts="2026-07-22T21:20:00Z"):
    return {
        "lat": lat,
        "lng": lng,
        "accuracy": accuracy,
        "speed": speed,
        "tracking_phase": phase,
        "timestamp": ts,
        "captured_at": ts,
    }


class TestFilterLowAccuracy:
    def test_drops_points_worse_than_threshold(self):
        pts = [_pt(LAT0, LNG0, accuracy=8), _pt(LAT0, LNG0, accuracy=80), _pt(LAT0, LNG0, accuracy=51)]
        kept, dropped, null_kept = filter_low_accuracy(pts, MAX_TRUSTED_ACCURACY_M)
        assert len(kept) == 1
        assert dropped == 2
        assert null_kept == 0

    def test_keeps_points_at_or_under_threshold(self):
        pts = [_pt(LAT0, LNG0, accuracy=50.0), _pt(LAT0, LNG0, accuracy=10.0)]
        kept, dropped, _ = filter_low_accuracy(pts)
        assert len(kept) == 2
        assert dropped == 0

    def test_null_accuracy_kept_and_counted(self):
        pts = [_pt(LAT0, LNG0, accuracy=None), _pt(LAT0, LNG0, accuracy=5)]
        kept, dropped, null_kept = filter_low_accuracy(pts)
        assert len(kept) == 2
        assert dropped == 0
        assert null_kept == 1

    def test_non_numeric_accuracy_treated_as_unknown(self):
        pts = [_pt(LAT0, LNG0, accuracy="bad")]
        kept, dropped, null_kept = filter_low_accuracy(pts)
        assert len(kept) == 1
        assert null_kept == 1

    def test_does_not_mutate_input_rows(self):
        pts = [_pt(LAT0, LNG0, accuracy=5)]
        original = dict(pts[0])
        filter_low_accuracy(pts)
        assert pts[0] == original


def _cluster_distance_km(points):
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += calculate_distance(a["lat"], a["lng"], b["lat"], b["lng"])
    return total


class TestCollapseStationaryClusters:
    def test_parking_lot_jitter_collapses_to_zero_distance(self):
        # Five fixes jittering within ~10 m, speed 0 — a parked car.
        jitter = [
            _pt(LAT0, LNG0, speed=0.0),
            _pt(LAT0 + 0.00005, LNG0 + 0.00003, speed=0.0),
            _pt(LAT0 - 0.00004, LNG0 + 0.00005, speed=0.0),
            _pt(LAT0 + 0.00002, LNG0 - 0.00006, speed=0.0),
            _pt(LAT0 - 0.00003, LNG0 - 0.00002, speed=0.0),
        ]
        raw_km = _cluster_distance_km(jitter)
        collapsed, count = collapse_stationary_clusters(jitter)
        assert count == 3  # 5 in → 2 out
        assert len(collapsed) == 2
        # The collapsed pair sits at one location → ~0 km, vs a non-trivial
        # raw scribble.
        assert _cluster_distance_km(collapsed) == 0.0
        assert raw_km > 0.0

    def test_moving_trace_is_untouched(self):
        moving = [
            _pt(LAT0, LNG0, speed=15.0),
            _pt(LAT0 + 0.001, LNG0, speed=15.0),  # ~111 m
            _pt(LAT0 + 0.002, LNG0, speed=15.0),
        ]
        collapsed, count = collapse_stationary_clusters(moving)
        assert count == 0
        assert len(collapsed) == 3

    def test_slow_crawl_above_speed_floor_not_collapsed(self):
        # Points close together but reporting real motion (2 m/s > floor).
        crawl = [
            _pt(LAT0, LNG0, speed=2.0),
            _pt(LAT0 + 0.00005, LNG0, speed=2.0),
            _pt(LAT0 + 0.0001, LNG0, speed=2.0),
        ]
        collapsed, count = collapse_stationary_clusters(crawl)
        assert count == 0
        assert len(collapsed) == 3

    def test_preserves_first_and_last_timestamp_and_phase(self):
        cluster = [
            _pt(LAT0, LNG0, speed=0.0, phase="arrived_at_pickup", ts="2026-07-22T21:20:00Z"),
            _pt(LAT0 + 0.00003, LNG0, speed=0.0, phase="arrived_at_pickup", ts="2026-07-22T21:20:20Z"),
            _pt(LAT0 - 0.00002, LNG0, speed=0.0, phase="trip_in_progress", ts="2026-07-22T21:20:40Z"),
        ]
        collapsed, _ = collapse_stationary_clusters(cluster)
        assert len(collapsed) == 2
        assert collapsed[0]["timestamp"] == "2026-07-22T21:20:00Z"
        assert collapsed[0]["tracking_phase"] == "arrived_at_pickup"
        assert collapsed[-1]["timestamp"] == "2026-07-22T21:20:40Z"
        assert collapsed[-1]["tracking_phase"] == "trip_in_progress"

    def test_stationary_without_speed_field_collapses_on_distance(self):
        cluster = [
            _pt(LAT0, LNG0, speed=None),
            _pt(LAT0 + 0.00004, LNG0, speed=None),
            _pt(LAT0 - 0.00003, LNG0, speed=None),
        ]
        collapsed, count = collapse_stationary_clusters(cluster)
        assert count == 1
        assert len(collapsed) == 2

    def test_single_point_unchanged(self):
        pts = [_pt(LAT0, LNG0)]
        collapsed, count = collapse_stationary_clusters(pts)
        assert count == 0
        assert collapsed == pts

    def test_stationary_then_moving_splits_correctly(self):
        pts = [
            _pt(LAT0, LNG0, speed=0.0),
            _pt(LAT0 + 0.00003, LNG0, speed=0.0),
            _pt(LAT0 + 0.00002, LNG0, speed=0.0),
            _pt(LAT0 + 0.002, LNG0, speed=15.0),  # departs
            _pt(LAT0 + 0.004, LNG0, speed=15.0),
        ]
        collapsed, count = collapse_stationary_clusters(pts)
        # 3-point stationary cluster → 2, plus the 2 moving points untouched.
        assert count == 1
        assert len(collapsed) == 4

    def test_does_not_mutate_input_rows(self):
        pts = [_pt(LAT0, LNG0, speed=0.0), _pt(LAT0 + 0.00003, LNG0, speed=0.0)]
        originals = [dict(p) for p in pts]
        collapse_stationary_clusters(pts)
        assert pts == originals
