"""Read-only, timestamp-authoritative analysis of one ride's GPS evidence.

This module deliberately has no database or routing-provider calls.  It sorts,
validates, and measures already-loaded evidence while treating server ride
lifecycle timestamps as phase authority.  Coordinate-bearing points remain on
the returned analysis object; the public ``report`` is safe to print.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, Tuple

try:
    from ..geo_utils import calculate_distance
except ImportError:
    from geo_utils import calculate_distance  # type: ignore

try:
    from .datetime_utils import parse_iso_utc
    from .route_segments import SegmentedRoute, segment_route
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.route_segments import SegmentedRoute, segment_route  # type: ignore


PHASE_NAMES = ("phase_1", "phase_2", "phase_3")
_STORED_PHASE_NAMES = {
    "phase_1": {"online_idle", "available", "period_1"},
    "phase_2": {"navigating_to_pickup", "driver_assigned", "period_2"},
    "phase_3": {"trip_in_progress", "period_3"},
}


@dataclass(frozen=True)
class RideRouteAnalysis:
    """Sanitized metrics plus internal coordinate-bearing accepted evidence."""

    report: Dict[str, Any]
    phase_points: Dict[str, Tuple[Dict[str, Any], ...]]
    segmented_phase_3: SegmentedRoute


def _boundaries(ride: Dict[str, Any]) -> tuple[datetime, datetime, datetime]:
    requested = parse_iso_utc(ride.get("ride_requested_at"))
    started = parse_iso_utc(ride.get("ride_started_at"))
    completed = parse_iso_utc(ride.get("ride_completed_at"))
    if requested is None or started is None or completed is None:
        raise ValueError("ride request, start, and completion timestamps are required")
    if started < requested:
        raise ValueError("ride start must not precede request")
    if completed <= started:
        raise ValueError("ride completion must be after start")
    return requested, started, completed


def _phase_for(
    captured_at: datetime,
    requested_at: datetime,
    started_at: datetime,
    completed_at: datetime,
) -> str:
    if captured_at < requested_at:
        return "phase_1"
    if captured_at < started_at:
        return "phase_2"
    if captured_at <= completed_at:
        return "phase_3"
    return "after_completion"


def _completion_point(ride: Dict[str, Any], completed_at: datetime) -> Dict[str, Any] | None:
    lat = ride.get("dropoff_lat")
    lng = ride.get("dropoff_lng")
    if lat is None or lng is None:
        return None
    return {
        "lat": lat,
        "lng": lng,
        "captured_at": completed_at.isoformat(),
        "recording_session_id": "diagnostic-completion-anchor",
        "sequence_number": 0,
        "accuracy": 0,
    }


def _observed_distance(segmented: SegmentedRoute) -> float:
    total = 0.0
    for segment in segmented.observed_segments:
        for left, right in zip(segment.points, segment.points[1:], strict=False):
            total += calculate_distance(
                float(left["lat"]),
                float(left["lng"]),
                float(right["lat"]),
                float(right["lng"]),
            )
    return round(total, 3)


def _accepted_points(segmented: SegmentedRoute) -> Tuple[Dict[str, Any], ...]:
    return tuple(point for segment in segmented.observed_segments for point in segment.points)


def _rejection_reasons(segmented: SegmentedRoute) -> Dict[str, int]:
    counts = Counter(rejected.reason for rejected in segmented.rejected_points)
    return dict(sorted(counts.items()))


def analyze_ride_evidence(
    ride: Dict[str, Any],
    locations: Iterable[Dict[str, Any]],
) -> RideRouteAnalysis:
    """Classify and measure GPS rows strictly by authoritative ride timestamps.

    The returned report contains no coordinates or addresses.  Segments never
    cross phase boundaries, and Phase 1/2 evidence cannot contribute to the
    passenger-trip (Phase 3) distance.
    """

    requested_at, started_at, completed_at = _boundaries(ride)
    buckets: Dict[str, list[Dict[str, Any]]] = {name: [] for name in PHASE_NAMES}
    excluded_after_completion = 0
    invalid_capture_time = 0
    stored_phase_disagreements = 0

    for point in locations:
        if not isinstance(point, dict):
            invalid_capture_time += 1
            continue
        captured_at = parse_iso_utc(point.get("captured_at") or point.get("timestamp"))
        if captured_at is None:
            invalid_capture_time += 1
            continue
        phase = _phase_for(captured_at, requested_at, started_at, completed_at)
        if phase == "after_completion":
            excluded_after_completion += 1
            continue
        buckets[phase].append(point)
        if point.get("tracking_phase") not in _STORED_PHASE_NAMES[phase]:
            stored_phase_disagreements += 1

    completion_point = _completion_point(ride, completed_at)
    segmented = {
        phase: segment_route(
            points,
            ride,
            completion_point if phase == "phase_3" else None,
        )
        for phase, points in buckets.items()
    }
    accepted = {phase: _accepted_points(value) for phase, value in segmented.items()}

    phase_report: Dict[str, Dict[str, Any]] = {}
    for phase, value in segmented.items():
        duration_seconds = None
        if phase == "phase_2":
            duration_seconds = int((started_at - requested_at).total_seconds())
        elif phase == "phase_3":
            duration_seconds = int((completed_at - started_at).total_seconds())
        phase_report[phase] = {
            "point_count": value.quality.point_count,
            "segment_count": value.quality.segment_count,
            "rejected_point_count": value.quality.rejected_point_count,
            "rejection_reasons": _rejection_reasons(value),
            "observed_distance_km": _observed_distance(value),
            "duration_seconds": duration_seconds,
            "max_gap_seconds": value.quality.max_gap_seconds,
            "coverage_ratio": value.quality.coverage_ratio,
        }

    strict_phase_3_km = float(phase_report["phase_3"]["observed_distance_km"])
    try:
        stored_actual_km = float(ride.get("actual_distance_km") or 0)
    except (TypeError, ValueError):
        stored_actual_km = 0.0
    contamination_ratio = round(stored_actual_km / strict_phase_3_km, 3) if strict_phase_3_km > 0 else None
    if contamination_ratio is not None and contamination_ratio >= 1.25:
        diagnosis = "likely_phase_contamination"
    elif strict_phase_3_km == 0:
        diagnosis = "insufficient_phase_3_evidence"
    else:
        diagnosis = "distance_consistent_with_phase_3"

    report = {
        "ride_id": str(ride.get("id") or ""),
        "lifecycle": {
            "requested_at": requested_at.isoformat(),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
        },
        "phases": phase_report,
        "excluded_after_completion_count": excluded_after_completion,
        "invalid_capture_time_count": invalid_capture_time,
        "stored_phase_disagreement_count": stored_phase_disagreements,
        "stored_actual_distance_km": round(stored_actual_km, 3),
        "strict_phase_3_observed_km": round(strict_phase_3_km, 3),
        "contamination_delta_km": round(stored_actual_km - strict_phase_3_km, 3),
        "contamination_ratio": contamination_ratio,
        "diagnosis": diagnosis,
    }
    return RideRouteAnalysis(report, accepted, segmented["phase_3"])
