"""Unit tests for the Period-1 batch incremental-distance helper."""

import pytest

from backend.utils.period1_distance import batch_incremental_distance_km

pytestmark = pytest.mark.unit


def test_empty_or_single_point_is_zero():
    assert batch_incremental_distance_km([]) == 0.0
    assert batch_incremental_distance_km([{"lat": 50.44, "lng": -104.62}]) == 0.0


def test_moving_batch_sums_distance():
    # ~0.001 deg lat ≈ 111 m per step; three steps ≈ 0.33 km.
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8},
        {"lat": 50.4410, "lng": -104.6200, "accuracy": 8},
        {"lat": 50.4420, "lng": -104.6200, "accuracy": 8},
        {"lat": 50.4430, "lng": -104.6200, "accuracy": 8},
    ]
    km = batch_incremental_distance_km(pts)
    assert 0.28 < km < 0.38


def test_stationary_jitter_collapses_to_zero():
    # A parked car: tiny sub-15 m wander at ~zero speed must not add distance.
    pts = [
        {"lat": 50.44000, "lng": -104.62000, "accuracy": 5, "speed": 0.0},
        {"lat": 50.44001, "lng": -104.62001, "accuracy": 5, "speed": 0.1},
        {"lat": 50.44000, "lng": -104.61999, "accuracy": 5, "speed": 0.0},
        {"lat": 50.44001, "lng": -104.62000, "accuracy": 5, "speed": 0.05},
    ]
    assert batch_incremental_distance_km(pts) < 0.02


def test_low_accuracy_fixes_dropped():
    # The middle fix is garbage (accuracy 500 m) and must be excluded, so the
    # distance is the clean A→C step, not the A→spike→C detour.
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8},
        {"lat": 50.4600, "lng": -104.6500, "accuracy": 500},
        {"lat": 50.4410, "lng": -104.6200, "accuracy": 8},
    ]
    km = batch_incremental_distance_km(pts)
    assert km < 0.2  # ~111 m A→C, not the ~5 km detour through the spike


def test_accepts_latitude_longitude_keys():
    pts = [
        {"latitude": 50.4400, "longitude": -104.6200, "accuracy": 8},
        {"latitude": 50.4420, "longitude": -104.6200, "accuracy": 8},
    ]
    assert batch_incremental_distance_km(pts) > 0.1
