"""Unit tests for the Period-1 batch incremental-distance helper."""

from datetime import datetime, timedelta, timezone

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


def test_teleportation_spike_dropped_with_timestamps():
    """A single fix that jumps 250 km away (Regina → Saskatoon) at impossible
    speed must be dropped. The return fix is compared against the last GOOD
    point, so it passes."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8, "timestamp": 1000},
        {"lat": 52.1300, "lng": -106.6700, "accuracy": 8, "timestamp": 1005},  # ~250 km in 5s
        {"lat": 50.4410, "lng": -104.6200, "accuracy": 8, "timestamp": 1010},
    ]
    km = batch_incremental_distance_km(pts)
    assert km < 0.2  # ~111 m A→C, not the ~500 km round-trip through Saskatoon


def test_teleportation_spike_dropped_without_timestamps():
    """Without timestamps, a 100+ km hop is rejected by the distance-only
    fallback (> 10 km single hop)."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8},
        {"lat": 51.4400, "lng": -104.6200, "accuracy": 8},  # ~111 km north
        {"lat": 50.4410, "lng": -104.6200, "accuracy": 8},
    ]
    km = batch_incremental_distance_km(pts)
    assert km < 0.2  # spike dropped, result is A→C only


def test_legitimate_fast_driving_kept():
    """A driver doing 100 km/h on the highway should NOT be filtered out."""
    # 1 km in 36 seconds = 100 km/h — well under the 200 km/h cap
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8, "timestamp": 1000},
        {"lat": 50.4490, "lng": -104.6200, "accuracy": 8, "timestamp": 1036},
        {"lat": 50.4580, "lng": -104.6200, "accuracy": 8, "timestamp": 1072},
    ]
    km = batch_incremental_distance_km(pts)
    assert km > 1.5  # ~2 km total, should all be kept


def test_sub_10km_spike_dropped_via_implied_speed():
    """The speed filter must actually run in the real pipeline.

    A 5 km jump in 5 s implies ~3600 km/h, but every individual hop stays under
    the 10 km distance-only fallback — so this case is caught by the implied-
    speed rule or not at all. It regressed when ``_normalize`` dropped the
    timestamp, which silently reduced the filter to its fallback and let the
    spike through as ~9.9 km of phantom deadhead.
    """
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8, "timestamp": 1000},
        {"lat": 50.4850, "lng": -104.6200, "accuracy": 8, "timestamp": 1005},  # ~5 km in 5 s
        {"lat": 50.4410, "lng": -104.6200, "accuracy": 8, "timestamp": 1010},
    ]
    km = batch_incremental_distance_km(pts)
    assert km < 0.2  # ~111 m A→C, not the ~9.9 km round trip through the spike


def test_v2_datetime_timestamps_drive_the_speed_filter():
    """v2 outbox rows carry ``captured_at`` as a ``datetime``, not a number."""
    base = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8, "captured_at": base},
        {
            "lat": 50.4850,
            "lng": -104.6200,
            "accuracy": 8,
            "captured_at": base + timedelta(seconds=5),
        },
        {
            "lat": 50.4410,
            "lng": -104.6200,
            "accuracy": 8,
            "captured_at": base + timedelta(seconds=10),
        },
    ]
    assert batch_incremental_distance_km(pts) < 0.2


def test_legacy_iso_string_timestamps_drive_the_speed_filter():
    """Legacy v1 payloads send ISO-8601 strings — ``float()`` can't read those."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8, "timestamp": "2026-08-27T12:00:00Z"},
        {"lat": 50.4850, "lng": -104.6200, "accuracy": 8, "timestamp": "2026-08-27T12:00:05Z"},
        {"lat": 50.4410, "lng": -104.6200, "accuracy": 8, "timestamp": "2026-08-27T12:00:10Z"},
    ]
    assert batch_incremental_distance_km(pts) < 0.2


def test_epoch_millisecond_timestamps_drive_the_speed_filter():
    """JS clients send ``Date.now()`` — milliseconds, not seconds."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8, "timestamp": 1_700_000_000_000},
        {"lat": 50.4850, "lng": -104.6200, "accuracy": 8, "timestamp": 1_700_000_005_000},
        {"lat": 50.4410, "lng": -104.6200, "accuracy": 8, "timestamp": 1_700_000_010_000},
    ]
    assert batch_incremental_distance_km(pts) < 0.2


def test_legitimate_capture_gap_is_not_zeroed():
    """A real 15 km deadhead across a capture gap must still be counted.

    The OS suspends location capture (Doze / backgrounded app) while the driver
    keeps driving, so one hop is long but its elapsed time makes it plausible
    (15 km in 600 s = 90 km/h). Dropping it would also strand the reference and
    zero every later fix in the batch — an under-count on the Period-1
    accumulator, which is the direction that matters for insurance audit.
    """
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "accuracy": 8, "timestamp": 1000},
        {"lat": 50.5750, "lng": -104.6200, "accuracy": 8, "timestamp": 1600},  # 90 km/h
        {"lat": 50.5760, "lng": -104.6200, "accuracy": 8, "timestamp": 1610},
        {"lat": 50.5770, "lng": -104.6200, "accuracy": 8, "timestamp": 1620},
        {"lat": 50.5780, "lng": -104.6200, "accuracy": 8, "timestamp": 1630},
    ]
    assert batch_incremental_distance_km(pts) > 15.0
