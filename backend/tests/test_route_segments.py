"""Pure contract tests for timestamp-ordered, gap-safe route segmentation."""

import os
from datetime import datetime, timedelta, timezone

try:
    from backend.utils.route_segments import segment_route
except ImportError:
    from utils.route_segments import segment_route  # type: ignore

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-ci-only-32chars!!")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass123!")

BASE_TIME = datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc)


def _point(
    seconds: int,
    *,
    session: str = "session-a",
    sequence: int = 0,
    lat: float = 50.445,
    lng: float = -104.618,
    accuracy: float = 8,
) -> dict:
    return {
        "recording_session_id": session,
        "sequence_number": sequence,
        "captured_at": (BASE_TIME + timedelta(seconds=seconds)).isoformat(),
        "lat": lat,
        "lng": lng,
        "accuracy": accuracy,
    }


def _lifecycle(completed_seconds: int = 120) -> dict:
    return {
        "ride_started_at": BASE_TIME.isoformat(),
        "ride_completed_at": (BASE_TIME + timedelta(seconds=completed_seconds)).isoformat(),
    }


def _flatten(segmented) -> list[dict]:
    return [point for segment in segmented.observed_segments for point in segment.points]


def test_orders_late_uploaded_points_by_capture_time_then_session_and_sequence():
    segmented = segment_route(
        [
            _point(20, session="session-b", sequence=2),
            _point(10, session="session-b", sequence=1),
            _point(10, session="session-a", sequence=4),
        ],
        _lifecycle(),
        completion_point=None,
    )

    assert [(point["recording_session_id"], point["sequence_number"]) for point in _flatten(segmented)] == [
        ("session-a", 4),
        ("session-b", 1),
        ("session-b", 2),
    ]


def test_rejects_duplicate_durable_identity_without_creating_coordinates():
    duplicated = _point(10, sequence=1)

    segmented = segment_route(
        [_point(0, sequence=0), duplicated, {**duplicated, "lat": 50.446}],
        _lifecycle(),
        completion_point=None,
    )

    assert len(_flatten(segmented)) == 2
    assert [(item.reason, item.sequence_number) for item in segmented.rejected_points] == [("duplicate_identity", 1)]


def test_splits_on_time_distance_and_impossible_speed_without_cross_gap_chords():
    segmented = segment_route(
        [
            _point(0, sequence=0),
            _point(61, sequence=1),
            _point(66, sequence=2, lat=50.450),
            _point(67, sequence=3, lat=50.4515),
        ],
        _lifecycle(),
        completion_point=None,
    )

    assert [segment.boundary_reason for segment in segmented.observed_segments] == [
        None,
        "time_gap",
        "distance_gap",
        "impossible_speed",
    ]
    assert [len(segment.points) for segment in segmented.observed_segments] == [1, 1, 1, 1]


def test_rejects_clock_regression_within_a_recording_session():
    segmented = segment_route(
        [_point(10, sequence=0), _point(5, sequence=1)],
        _lifecycle(),
        completion_point=None,
    )

    assert [(point["sequence_number"], point["captured_at"]) for point in _flatten(segmented)] == [
        (0, _point(10)["captured_at"])
    ]
    assert [(item.reason, item.sequence_number) for item in segmented.rejected_points] == [("clock_regression", 1)]


def test_session_boundary_is_an_explicit_segment_boundary():
    segmented = segment_route(
        [_point(0, session="session-a"), _point(10, session="session-b")],
        _lifecycle(),
        completion_point=None,
    )

    assert [segment.boundary_reason for segment in segmented.observed_segments] == [None, "session_boundary"]


def test_coverage_and_completion_tail_use_accuracy_tolerance():
    completion = _point(120, sequence=2, lat=50.4458, accuracy=20)
    segmented = segment_route(
        [_point(0, sequence=0), _point(60, sequence=1), _point(120, sequence=2, lat=50.4455, accuracy=30)],
        _lifecycle(),
        completion_point=completion,
    )

    assert segmented.quality.coverage_ratio == 1.0
    assert segmented.quality.missing_tail is False
    assert segmented.quality.completion_distance_m is not None


def test_missing_or_stale_tail_is_not_reported_as_complete_coverage():
    missing = segment_route(
        [_point(0, sequence=0), _point(30, sequence=1)],
        _lifecycle(),
        completion_point={"missing_tail": True},
    )
    stale = segment_route(
        [_point(0, sequence=0), _point(30, sequence=1)],
        _lifecycle(completed_seconds=120),
        completion_point=None,
    )

    assert missing.quality.missing_tail is True
    assert stale.quality.missing_tail is True
    assert stale.quality.coverage_ratio == 0.25


def test_legacy_v1_points_accepted_with_synthetic_identity():
    """V1 breadcrumbs stored via persist_ride_breadcrumbs have NULL
    recording_session_id and sequence_number, and use 'timestamp' instead
    of 'captured_at'. The parser must accept them with synthetic identities."""
    v1_points = [
        {"lat": 50.445, "lng": -104.618, "accuracy": 8, "timestamp": (BASE_TIME + timedelta(seconds=0)).isoformat()},
        {"lat": 50.4451, "lng": -104.618, "accuracy": 8, "timestamp": (BASE_TIME + timedelta(seconds=30)).isoformat()},
        {"lat": 50.4452, "lng": -104.618, "accuracy": 8, "timestamp": (BASE_TIME + timedelta(seconds=60)).isoformat()},
    ]

    segmented = segment_route(v1_points, _lifecycle(), completion_point=None)

    assert len(_flatten(segmented)) == 3
    assert segmented.quality.rejected_point_count == 0
    assert segmented.quality.point_count == 3


def test_shuffled_legacy_points_are_ordered_by_timestamp_without_false_clock_regressions():
    def legacy_point(seconds: int) -> dict:
        return {
            "lat": 50.445 + seconds * 0.000001,
            "lng": -104.618,
            "accuracy": 8,
            "timestamp": (BASE_TIME + timedelta(seconds=seconds)).isoformat(),
        }

    points = [legacy_point(120), legacy_point(0), legacy_point(60)]
    segmented = segment_route(points, _lifecycle(), completion_point=None)

    assert [point["timestamp"] for point in _flatten(segmented)] == [
        legacy_point(0)["timestamp"],
        legacy_point(60)["timestamp"],
        legacy_point(120)["timestamp"],
    ]
    assert segmented.quality.rejected_point_count == 0


def test_legacy_v1_points_without_captured_at_fall_back_to_timestamp():
    v1_point = {
        "lat": 50.445,
        "lng": -104.618,
        "accuracy": 8,
        "timestamp": (BASE_TIME + timedelta(seconds=10)).isoformat(),
    }
    segmented = segment_route([v1_point], _lifecycle(), completion_point=None)

    flat = _flatten(segmented)
    assert len(flat) == 1
    assert segmented.quality.rejected_point_count == 0


def test_legacy_v1_points_with_explicit_none_identity_treated_as_legacy():
    point = {
        "lat": 50.445,
        "lng": -104.618,
        "accuracy": 8,
        "recording_session_id": None,
        "sequence_number": None,
        "timestamp": (BASE_TIME + timedelta(seconds=5)).isoformat(),
    }

    segmented = segment_route([point], _lifecycle(), completion_point=None)
    assert len(_flatten(segmented)) == 1
    assert segmented.quality.rejected_point_count == 0


def test_legacy_buffer_receive_times_do_not_create_false_impossible_speed_boundaries():
    points = [
        {
            "lat": 50.445 + index * 0.0001,
            "lng": -104.618,
            "accuracy": 8,
            # Legacy WS batches assigned server receive time inside a tight
            # insert loop, so this orders points but is not capture cadence.
            "timestamp": (BASE_TIME + timedelta(microseconds=index)).isoformat(),
        }
        for index in range(10)
    ]

    segmented = segment_route(points, _lifecycle(), completion_point=None)

    assert len(segmented.observed_segments) == 1
    assert len(_flatten(segmented)) == 10
    assert segmented.quality.rejected_point_count == 0


def test_completion_fix_is_ordered_after_late_flushed_legacy_points():
    legacy_before = {
        "lat": 50.445,
        "lng": -104.618,
        "timestamp": (BASE_TIME + timedelta(seconds=10)).isoformat(),
    }
    completion_fix = _point(20, sequence=0, lat=50.4452)
    completion_fix["is_completion_fix"] = True
    legacy_flushed_after_completion = {
        "lat": 50.4451,
        "lng": -104.618,
        "timestamp": (BASE_TIME + timedelta(seconds=30)).isoformat(),
    }

    segmented = segment_route(
        [legacy_before, completion_fix, legacy_flushed_after_completion],
        _lifecycle(),
        completion_point=completion_fix,
    )

    flattened = _flatten(segmented)
    assert flattened[-1]["is_completion_fix"] is True
    assert [segment.boundary_reason for segment in segmented.observed_segments] == [None, "session_boundary"]


def test_49_minute_fixture_keeps_two_long_gaps_and_a_5km_jump_separate():
    points = [
        _point(0, sequence=0),
        _point(60, sequence=1, lat=50.4451),
        _point(27 * 60, sequence=2, lat=50.4452),  # 26 minute gap
        _point(28 * 60, sequence=3, lat=50.4453),
        _point(38 * 60, sequence=4, lat=50.4454),  # 10 minute gap
        _point(39 * 60, sequence=5, lat=50.4927),  # approximately 5.25 km jump
    ]

    segmented = segment_route(points, _lifecycle(completed_seconds=49 * 60), completion_point=None)

    assert [segment.boundary_reason for segment in segmented.observed_segments] == [
        None,
        "time_gap",
        "time_gap",
        "distance_gap",
    ]
    flattened = _flatten(segmented)
    assert len(flattened) == len(points)
    assert {(point["lat"], point["lng"]) for point in flattened} == {(point["lat"], point["lng"]) for point in points}
    assert segmented.quality.max_gap_seconds == 26 * 60


# --- Low-accuracy rejection + gap-aware coverage (P1.3) -----------------------

def test_low_accuracy_points_rejected():
    segmented = segment_route(
        [
            _point(0, sequence=0, accuracy=8),
            _point(20, sequence=1, accuracy=80),   # too imprecise → rejected
            _point(40, sequence=2, accuracy=120),  # too imprecise → rejected
            _point(60, sequence=3, accuracy=12),
        ],
        _lifecycle(),
        None,
    )
    reasons = [r.reason for r in segmented.rejected_points]
    assert reasons.count("low_accuracy") == 2
    assert segmented.quality.point_count == 2  # only the two precise fixes remain


def test_missing_accuracy_is_kept():
    pts = [
        {"recording_session_id": "s", "sequence_number": 0, "lat": 50.445, "lng": -104.618,
         "captured_at": BASE_TIME.isoformat()},
        {"recording_session_id": "s", "sequence_number": 1, "lat": 50.4451, "lng": -104.618,
         "captured_at": (BASE_TIME + timedelta(seconds=30)).isoformat()},
    ]
    segmented = segment_route(pts, _lifecycle(), None)
    assert segmented.quality.rejected_point_count == 0
    assert segmented.quality.point_count == 2


def test_gap_aware_coverage_discounts_mid_trip_dead_zone():
    # Two fixes near the start, then a long silence, then two near the end.
    # First→last span covers the whole trip (span_coverage ~1.0), but a ~200 s
    # internal dead zone must drop the gap-aware coverage well below that.
    pts = [
        _point(0, sequence=0),
        _point(20, sequence=1),
        _point(240, sequence=2),   # 220 s gap — the missing middle
        _point(260, sequence=3),
    ]
    segmented = segment_route(pts, _lifecycle(completed_seconds=260), None)
    q = segmented.quality
    assert q.span_coverage_ratio == 1.0                 # fixes at both ends
    assert q.coverage_ratio < 0.3                       # but the middle is gone
    assert q.coverage_ratio < q.span_coverage_ratio


def test_continuous_trace_coverage_matches_span():
    # No gap beyond the 60 s threshold → gap-aware equals span.
    pts = [_point(i * 30, sequence=i) for i in range(5)]  # 0,30,60,90,120
    segmented = segment_route(pts, _lifecycle(completed_seconds=120), None)
    q = segmented.quality
    assert q.coverage_ratio == q.span_coverage_ratio == 1.0


# --- Spike despike (stale/duplicate fix "going back") -------------------------

def test_isolated_spike_is_dropped_not_drawn():
    # A moves forward; a stale fix then jumps ~1.3 km BACK in 0.3 s (impossible),
    # then the trace continues plausibly from A. The spike must be dropped so it
    # never becomes a drawn vertex, leaving one continuous segment.
    pts = [
        _point(0, sequence=0, lat=50.4100, lng=-104.6500),
        {  # the spike — 0.3 s later, ~1.3 km away, back toward the start
            "recording_session_id": "session-a", "sequence_number": 1,
            "captured_at": (BASE_TIME + timedelta(seconds=0.3)).isoformat(),
            "lat": 50.4200, "lng": -104.6600, "accuracy": 10,
        },
        _point(12, sequence=2, lat=50.4101, lng=-104.6499),
        _point(24, sequence=3, lat=50.4102, lng=-104.6498),
    ]
    segmented = segment_route(pts, _lifecycle(completed_seconds=30), None)
    reasons = [r.reason for r in segmented.rejected_points]
    assert "spike_outlier" in reasons
    drawn = [(p["lat"], p["lng"]) for s in segmented.observed_segments for p in s.points]
    assert (50.4200, -104.6600) not in drawn          # spike never drawn
    assert len(segmented.observed_segments) == 1        # one continuous route
    assert [len(s.points) for s in segmented.observed_segments] == [3]


def test_sustained_teleport_is_not_despiked():
    # Both sides impossible (a real outage/teleport, not a there-and-back spike)
    # → left for the boundary logic, not dropped.
    pts = [
        _point(0, sequence=0, lat=50.4100, lng=-104.6500),
        _point(1, sequence=1, lat=50.5000, lng=-104.7000),   # far jump
        _point(2, sequence=2, lat=50.5001, lng=-104.7001),   # stays there
    ]
    segmented = segment_route(pts, _lifecycle(completed_seconds=30), None)
    assert "spike_outlier" not in [r.reason for r in segmented.rejected_points]
