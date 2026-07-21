"""Contract tests for timestamp-authoritative ride route analysis."""

from datetime import datetime, timedelta, timezone

import pytest

try:
    from backend.utils.ride_route_analyzer import analyze_ride_evidence
except ImportError:
    from utils.ride_route_analyzer import analyze_ride_evidence  # type: ignore


BASE = datetime(2026, 7, 21, 17, 0, tzinfo=timezone.utc)


def _ride() -> dict:
    return {
        "id": "ride-test",
        "ride_requested_at": BASE.isoformat(),
        "ride_started_at": (BASE + timedelta(seconds=60)).isoformat(),
        "ride_completed_at": (BASE + timedelta(seconds=180)).isoformat(),
        "pickup_lat": 50.45,
        "pickup_lng": -104.61,
        "dropoff_lat": 50.45,
        "dropoff_lng": -104.53,
        "planned_distance_km": 6.1,
        "actual_distance_km": 21.3,
    }


def _point(
    seconds: int,
    lng: float,
    sequence: int,
    stored_phase: str = "trip_in_progress",
) -> dict:
    return {
        "captured_at": (BASE + timedelta(seconds=seconds)).isoformat(),
        "recording_session_id": "session-1",
        "sequence_number": sequence,
        "lat": 50.45,
        "lng": lng,
        "tracking_phase": stored_phase,
        "accuracy": 8,
    }


def _phase_3_trace(*, first_sequence: int = 10) -> list[dict]:
    """A 120-second eastbound trace with plausible four-second spacing."""
    points = []
    for index, seconds in enumerate(range(60, 181, 4)):
        progress = (seconds - 60) / 120
        points.append(_point(seconds, -104.61 + 0.08 * progress, first_sequence + index))
    return points


def test_lifecycle_timestamps_are_the_phase_authority():
    points = [
        _point(-1, -104.62, 1),
        _point(0, -104.61, 2),
        _point(59, -104.61, 3),
        *_phase_3_trace(),
        _point(181, -104.52, 50),
    ]

    analysis = analyze_ride_evidence(_ride(), points)

    assert analysis.report["phases"]["phase_1"]["point_count"] == 1
    assert analysis.report["phases"]["phase_2"]["point_count"] == 2
    assert analysis.report["phases"]["phase_3"]["point_count"] == 31
    assert analysis.report["excluded_after_completion_count"] == 1
    assert analysis.report["phases"]["phase_2"]["observed_distance_km"] == 0
    assert analysis.report["phases"]["phase_3"]["observed_distance_km"] > 5
    assert analysis.report["phases"]["phase_2"]["duration_seconds"] == 60
    assert analysis.report["phases"]["phase_3"]["duration_seconds"] == 120
    assert analysis.report["stored_phase_disagreement_count"] >= 3
    assert analysis.report["diagnosis"] == "likely_phase_contamination"


def test_no_distance_segment_crosses_a_phase_boundary():
    analysis = analyze_ride_evidence(
        _ride(),
        [
            _point(59, -104.70, 1, "navigating_to_pickup"),
            *_phase_3_trace(first_sequence=2),
        ],
    )

    assert analysis.report["phases"]["phase_2"]["observed_distance_km"] == 0
    assert analysis.report["phases"]["phase_3"]["observed_distance_km"] > 5


def test_capture_timestamp_wins_over_client_tracking_phase():
    points = [
        _point(0, -104.61, 1, "trip_in_progress"),
        _point(59, -104.61, 2, "online_idle"),
        *_phase_3_trace(first_sequence=3),
    ]

    analysis = analyze_ride_evidence(_ride(), points)

    assert analysis.report["phases"]["phase_2"]["point_count"] == 2
    assert analysis.report["stored_phase_disagreement_count"] == 2


def test_invalid_lifecycle_fails_loudly():
    ride = _ride()
    ride["ride_completed_at"] = ride["ride_started_at"]

    with pytest.raises(ValueError, match="completion must be after start"):
        analyze_ride_evidence(ride, [])
