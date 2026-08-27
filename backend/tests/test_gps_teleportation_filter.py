"""Unit tests for the teleportation-spike GPS filter."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.utils.gps_filtering import filter_teleportation_spikes, point_epoch_seconds

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


def test_untimed_capture_gap_does_not_zero_the_batch_tail():
    """A real, sustained move with no timestamps must not strand the reference.

    Without a bound, every fix after one >10 km hop is also >10 km from the
    stale reference, so the whole tail is dropped and the batch contributes
    0 km. After MAX_CONSECUTIVE_SPIKE_DROPS disagreements the reference is
    presumed stale and the filter re-anchors, bounding the loss.
    """
    pts = [
        {"lat": 50.4400, "lng": -104.6200},
        {"lat": 50.5800, "lng": -104.6200},  # ~15.6 km — no timing to justify it
        {"lat": 50.5900, "lng": -104.6200},
        {"lat": 50.6000, "lng": -104.6200},
        {"lat": 50.6100, "lng": -104.6200},  # 4th disagreement → re-anchor here
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 3
    assert len(kept) == 2
    assert kept[-1]["lat"] == 50.6100


def test_spike_and_return_still_wins_over_the_reanchor_guard():
    """The re-anchor guard must not weaken the core spike-and-return case."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000},
        {"lat": 52.1300, "lng": -106.6700, "timestamp": 1005},  # spike
        {"lat": 50.4410, "lng": -104.6200, "timestamp": 1010},  # back near start
        {"lat": 50.4420, "lng": -104.6200, "timestamp": 1020},
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 1
    assert len(kept) == 3


def test_out_of_order_timestamps_fall_back_to_distance():
    """Elapsed time is unusable when fixes arrive out of order — the coarse
    distance cap still has to catch a wild hop rather than divide by a
    negative interval."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000},
        {"lat": 51.4400, "lng": -104.6200, "timestamp": 990},  # ~111 km, earlier ts
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 1
    assert len(kept) == 1


def test_null_coord_point_advances_the_timestamp_reference():
    """A fix with unusable coordinates must still advance ``prev_ts``.

    If it doesn't, the next hop is measured against a much older timestamp; the
    inflated elapsed time makes a spike's implied speed look survivable and it
    slips through. Here D is ~2 km from C in 5 s (~1440 km/h) but only
    ~7 km/h against the stale t=1000 reference.
    """
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000},
        {"lat": None, "lng": None, "timestamp": 2000},
        {"lat": 50.4410, "lng": -104.6200, "timestamp": 2005},
        {"lat": 50.4590, "lng": -104.6200, "timestamp": 2010},  # ~2 km in 5 s
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 1
    assert kept[-1]["lat"] == 50.4410


def test_point_epoch_seconds_reads_every_production_shape():
    """One accessor covers each timestamp shape the pipeline actually emits."""
    assert point_epoch_seconds({"ts": 1700000000.0}) == 1700000000.0
    assert point_epoch_seconds({"timestamp": 1700000000}) == 1700000000.0
    # Milliseconds (JS Date.now()) are scaled down, not read as the year 55839.
    assert point_epoch_seconds({"timestamp": 1_700_000_000_000}) == 1700000000.0
    # datetime (v2 outbox rows) and ISO-8601 strings (legacy v1 / Supabase).
    dt = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
    assert point_epoch_seconds({"captured_at": dt}) == dt.timestamp()
    assert point_epoch_seconds({"timestamp": "2026-08-27T12:00:00Z"}) == dt.timestamp()
    # Unusable values yield None rather than raising — the fix is still usable,
    # it just can't support a speed claim.
    assert point_epoch_seconds({}) is None
    assert point_epoch_seconds({"timestamp": "not-a-date"}) is None
    assert point_epoch_seconds({"timestamp": None}) is None


def test_burst_fixes_sharing_an_instant_are_not_spikes():
    """Two fixes metres apart but microseconds apart in time are normal.

    Batched/fused location providers emit bursts that share a capture instant,
    so the raw elapsed time divides ~11 m out to over a million km/h. Judging
    such a pair on raw speed drops legitimate movement (it broke the v2 idle
    route's accumulator test); the interval is clamped to the fastest sampling
    rate we believe instead.
    """
    base = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
    pts = [
        {"lat": 52.1300, "lng": -106.6700, "captured_at": base.isoformat()},
        # ~11 m away, 30 microseconds later
        {"lat": 52.1301, "lng": -106.6700, "captured_at": (base + timedelta(microseconds=30)).isoformat()},
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 0
    assert len(kept) == 2


def test_identical_timestamps_still_catch_a_real_jump():
    """Clamping the interval must not blind the filter to a genuine spike.

    The spike shares its predecessor's capture instant (a burst), so only the
    clamp decides it: ~250 km in one sampling interval is still impossible.
    """
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000},
        {"lat": 52.1300, "lng": -106.6700, "timestamp": 1000},  # ~250 km, same instant
        {"lat": 50.4410, "lng": -104.6200, "timestamp": 1005},  # ~111 m from A in 5 s
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 1
    assert len(kept) == 2
    assert kept[-1]["lat"] == 50.4410


def test_sub_second_interval_still_catches_a_short_spike():
    """A 500 m jump in 0.5 s is 3600 km/h — under the 10 km distance fallback,
    so only the clamped speed rule can catch it."""
    pts = [
        {"lat": 50.4400, "lng": -104.6200, "timestamp": 1000.0},
        {"lat": 50.4445, "lng": -104.6200, "timestamp": 1000.5},  # ~500 m in 0.5 s
        {"lat": 50.4401, "lng": -104.6200, "timestamp": 1001.0},
    ]
    kept, dropped = filter_teleportation_spikes(pts)
    assert dropped == 1
    assert len(kept) == 2
