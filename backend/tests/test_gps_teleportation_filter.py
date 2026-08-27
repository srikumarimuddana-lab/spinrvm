"""Unit tests for the teleportation-spike GPS filter."""

import pytest

from backend.utils.gps_filtering import filter_teleportation_spikes

pytestmark = pytest.mark.unit


def test_empty_and_single():
    assert filter_teleportation_spikes([]) == ([], 0)
    p = [{"lat": 50.44, "lng": -104.62}]
    kept, dropped = filter_teleportation_spikes(p)
    assert len(kept) == 1
    assert dropped == 0


def test_normal_driving_kept():
    """Normal city driving — all points should be kept."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000},
        {"lat": 50.4405, "lng": -104.6200, "timestamp": 1010},
        {"lat": 50.4410, "lng": -104.6200, "timestamp": 1020},
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert len(kept) == 3
    assert dropped == 0


def test_spike_with_timestamps_dropped():
    """A 250 km jump in 5 seconds → ~180,000 km/h. Must be dropped."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000},
        {"lat": 52.1300, "lng": -106.6700, "timestamp": 1005},  # spike
        {"lat": 50.4410, "lng": -104.6200, "timestamp": 1010},  # back to normal
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 1
    assert len(kept) == 2
    # Only first and third points kept
    assert kept[0]["lat"] == 50.4400
    assert kept[1]["lat"] == 50.4410


def test_spike_without_timestamps_distance_fallback():
    """No timestamps — falls back to >10 km hop rejection."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200},
        {"lat": 51.5000, "lng": -104.6200},  # ~118 km north
        {"lat": 50.4410, "lng": -104.6200},
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 1
    assert len(kept) == 2


def test_highway_speed_not_filtered():
    """100 km/h on highway — well under 200 km/h cap."""
    # ~1 km in 36 seconds = 100 km/h
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000},
        {"lat": 50.4490, "lng": -104.6200, "timestamp": 1036},
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 0
    assert len(kept) == 2


def test_multiple_consecutive_spikes():
    """Two consecutive spikes — both should be dropped."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000},
        {"lat": 52.0000, "lng": -106.0000, "timestamp": 1005},  # spike 1
        {"lat": 53.0000, "lng": -108.0000, "timestamp": 1010},  # spike 2 (compared to last GOOD point)
        {"lat": 50.4410, "lng": -104.6200, "timestamp": 1015},  # back to normal
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 2
    assert len(kept) == 2


def test_uses_recorded_at_fallback():
    """Should also work with 'recorded_at' timestamp key."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "recorded_at": 1000},
        {"lat": 52.1300, "lng": -106.6700, "recorded_at": 1005},  # spike
        {"lat": 50.4410, "lng": -104.6200, "recorded_at": 1010},
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 1


def test_custom_max_speed():
    """A lower speed cap should catch moderately fast movement."""
    # ~1.1 km in 10s = ~400 km/h — above a 50 km/h cap
    # Third point is close to first so after spike dropped it passes
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000},
        {"lat": 50.4500, "lng": -104.6200, "timestamp": 1010},  # ~1.1 km in 10s = ~400 km/h → dropped
        {"lat": 50.4401, "lng": -104.6200, "timestamp": 1020},  # ~11m from first in 20s → kept
    ]
    kept, dropped = filter_teleportation_spikes(pts, max_speed_kmh=50.0)
    assert dropped == 1
