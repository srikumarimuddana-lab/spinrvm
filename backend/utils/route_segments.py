"""Pure, gap-safe segmentation for durable trip GPS breadcrumbs.

This module deliberately does not call a database or routing provider. It
orders immutable device capture timestamps, rejects invalid/replayed evidence,
and returns only observed coordinates grouped into continuous segments. A later
finalizer may map-match each segment, but must never join them across a gap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from .datetime_utils import parse_iso_utc
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore


MAX_CONTINUOUS_GAP_SECONDS = 60
MAX_CONTINUOUS_DISPLACEMENT_METERS = 300
MAX_PLAUSIBLE_SPEED_KPH = 180


@dataclass(frozen=True)
class RejectedRoutePoint:
    """Non-sensitive reason for excluding one durable point from geometry."""

    recording_session_id: str
    sequence_number: int | None
    reason: str


@dataclass(frozen=True)
class ObservedRouteSegment:
    """An ordered run of adjacent points with no synthesized coordinates."""

    points: Tuple[Dict[str, Any], ...]
    boundary_reason: str | None


@dataclass(frozen=True)
class RouteSegmentationQuality:
    point_count: int
    segment_count: int
    rejected_point_count: int
    coverage_ratio: float
    max_gap_seconds: int
    missing_tail: bool
    completion_distance_m: float | None
    completion_tolerance_m: float | None


@dataclass(frozen=True)
class SegmentedRoute:
    observed_segments: Tuple[ObservedRouteSegment, ...]
    rejected_points: Tuple[RejectedRoutePoint, ...]
    quality: RouteSegmentationQuality


@dataclass(frozen=True)
class _ParsedPoint:
    point: Dict[str, Any]
    captured_at: datetime
    recording_session_id: str
    sequence_number: int
    input_index: int
    is_legacy: bool


def point_order(point: Dict[str, Any]) -> tuple[datetime, str, int]:
    """Canonical timestamp-first ordering for late/offline GPS delivery."""
    captured_at = parse_iso_utc(point.get("captured_at"))
    if captured_at is None:
        raise ValueError("captured_at must be a valid timestamp")
    return captured_at, str(point["recording_session_id"]), int(point["sequence_number"])


def completion_tolerance_m(final: Dict[str, Any], completion: Dict[str, Any]) -> float:
    """Accuracy-aware endpoint tolerance from the approved route contract."""
    return max(75.0, float(final.get("accuracy") or 0) + float(completion.get("accuracy") or 0) + 25.0)


def _as_coordinate(point: Dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(point["lat"])
        lng = float(point["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lng):
        return None
    if not -90 <= lat <= 90 or not -180 <= lng <= 180 or (lat == 0 and lng == 0):
        return None
    return lat, lng


def _distance_meters(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    left_coordinate = _as_coordinate(left)
    right_coordinate = _as_coordinate(right)
    if left_coordinate is None or right_coordinate is None:
        return math.inf
    lat1, lng1 = left_coordinate
    lat2, lng2 = right_coordinate
    radius_meters = 6_371_000.0
    delta_lat = math.radians(lat2 - lat1)
    delta_lng = math.radians(lng2 - lng1)
    sin_lat = math.sin(delta_lat / 2)
    sin_lng = math.sin(delta_lng / 2)
    a = sin_lat * sin_lat + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * sin_lng * sin_lng
    return radius_meters * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _reject(point: Dict[str, Any], reason: str) -> RejectedRoutePoint:
    raw_sequence = point.get("sequence_number")
    sequence_number = raw_sequence if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool) else None
    return RejectedRoutePoint(str(point.get("recording_session_id") or ""), sequence_number, reason)


def _parse_points(points: Iterable[Dict[str, Any]]) -> tuple[List[_ParsedPoint], List[RejectedRoutePoint]]:
    parsed: List[_ParsedPoint] = []
    rejected: List[RejectedRoutePoint] = []
    identities = set()

    for input_index, point in enumerate(points):
        if not isinstance(point, dict):
            rejected.append(RejectedRoutePoint("", None, "invalid_point"))
            continue
        session_id = point.get("recording_session_id")
        sequence_number = point.get("sequence_number")
        is_legacy = session_id is None and sequence_number is None
        if is_legacy:
            session_id = "legacy"
            sequence_number = input_index
        elif (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(sequence_number, int)
            or isinstance(sequence_number, bool)
            or sequence_number < 0
        ):
            rejected.append(_reject(point, "invalid_identity"))
            continue
        captured_at = parse_iso_utc(point.get("captured_at") or point.get("timestamp"))
        if captured_at is None:
            rejected.append(_reject(point, "invalid_capture_time"))
            continue
        if _as_coordinate(point) is None:
            rejected.append(_reject(point, "invalid_coordinate"))
            continue
        identity = (session_id, sequence_number)
        if identity in identities:
            rejected.append(_reject(point, "duplicate_identity"))
            continue
        identities.add(identity)
        parsed.append(_ParsedPoint(point, captured_at, session_id, sequence_number, input_index, is_legacy))

    # Sequence numbers are monotonic for a recording session. A newer sequence
    # with an older device timestamp is clock regression, not a route reversal.
    accepted: List[_ParsedPoint] = []
    for candidate in sorted(
        parsed, key=lambda item: (item.recording_session_id, item.sequence_number, item.input_index)
    ):
        prior = next(
            (point for point in reversed(accepted) if point.recording_session_id == candidate.recording_session_id),
            None,
        )
        if prior is not None and not candidate.is_legacy and candidate.captured_at < prior.captured_at:
            rejected.append(_reject(candidate.point, "clock_regression"))
            continue
        accepted.append(candidate)

    # A completion fix is captured before the WebSocket buffer necessarily
    # finishes flushing. Legacy rows received just afterward can therefore
    # carry a later server timestamp even though they precede trip completion.
    # Keep timestamp order within normal evidence, but enforce the route
    # contract that an explicit completion fix is the final evidence point.
    accepted.sort(
        key=lambda item: (
            item.point.get("is_completion_fix") is True,
            item.captured_at,
            item.recording_session_id,
            item.sequence_number,
        )
    )
    return accepted, rejected


def _boundary_reason(previous: _ParsedPoint, current: _ParsedPoint) -> tuple[str | None, int]:
    if previous.recording_session_id != current.recording_session_id:
        return "session_boundary", max(0, int((current.captured_at - previous.captured_at).total_seconds()))

    elapsed_seconds = max(0, int((current.captured_at - previous.captured_at).total_seconds()))
    if elapsed_seconds > MAX_CONTINUOUS_GAP_SECONDS:
        return "time_gap", elapsed_seconds
    displacement_meters = _distance_meters(previous.point, current.point)
    if displacement_meters > MAX_CONTINUOUS_DISPLACEMENT_METERS:
        return "distance_gap", elapsed_seconds
    # Legacy WebSocket batches did not carry device capture timestamps. The
    # server assigned each row a receive timestamp inside one tight insert
    # loop, which preserves point order but makes elapsed time (and therefore
    # calculated speed) meaningless within the batch. Retain the independent
    # time-gap and displacement guardrails above; skip only the speed-derived
    # boundary when both adjacent points use that legacy identity.
    if previous.is_legacy and current.is_legacy:
        return None, elapsed_seconds
    if elapsed_seconds == 0:
        if displacement_meters > 0:
            return "impossible_speed", elapsed_seconds
        return None, elapsed_seconds
    speed_kph = displacement_meters / elapsed_seconds * 3.6
    if speed_kph > MAX_PLAUSIBLE_SPEED_KPH:
        return "impossible_speed", elapsed_seconds
    return None, elapsed_seconds


def _coverage_ratio(points: List[_ParsedPoint], lifecycle: Dict[str, Any]) -> float:
    if not points:
        return 0.0
    started_at = parse_iso_utc(lifecycle.get("ride_started_at") or lifecycle.get("started_at"))
    completed_at = parse_iso_utc(lifecycle.get("ride_completed_at") or lifecycle.get("completed_at"))
    if started_at is None or completed_at is None or completed_at <= started_at:
        return 0.0
    first = max(points[0].captured_at, started_at)
    last = min(points[-1].captured_at, completed_at)
    if last <= first:
        return 0.0
    return round(min(1.0, (last - first).total_seconds() / (completed_at - started_at).total_seconds()), 3)


def _tail_quality(
    points: List[_ParsedPoint], lifecycle: Dict[str, Any], completion_point: Optional[Dict[str, Any]]
) -> tuple[bool, float | None, float | None]:
    if not points:
        return True, None, None
    final_point = points[-1].point
    if completion_point and completion_point.get("missing_tail") is True:
        return True, None, None
    if completion_point and _as_coordinate(completion_point) is not None:
        distance_meters = round(_distance_meters(final_point, completion_point), 1)
        tolerance_meters = completion_tolerance_m(final_point, completion_point)
        return distance_meters > tolerance_meters, distance_meters, tolerance_meters

    completed_at = parse_iso_utc(lifecycle.get("ride_completed_at") or lifecycle.get("completed_at"))
    if completed_at is None:
        return True, None, None
    tail_age = (completed_at - points[-1].captured_at).total_seconds()
    return tail_age > MAX_CONTINUOUS_GAP_SECONDS, None, None


def segment_route(
    points: Iterable[Dict[str, Any]], lifecycle: Dict[str, Any], completion_point: Optional[Dict[str, Any]]
) -> SegmentedRoute:
    """Create timestamp-ordered observed segments without crossing evidence gaps."""
    ordered, rejected = _parse_points(points)
    segments: List[ObservedRouteSegment] = []
    current_points: List[Dict[str, Any]] = []
    current_boundary: str | None = None
    max_gap_seconds = 0
    previous: _ParsedPoint | None = None

    for item in ordered:
        if not current_points:
            current_points.append(item.point)
            previous = item
            continue

        assert previous is not None
        boundary, elapsed_seconds = _boundary_reason(previous, item)
        max_gap_seconds = max(max_gap_seconds, elapsed_seconds)
        if boundary is not None:
            segments.append(ObservedRouteSegment(tuple(current_points), current_boundary))
            current_points = [item.point]
            current_boundary = boundary
        else:
            current_points.append(item.point)
        previous = item

    if current_points:
        segments.append(ObservedRouteSegment(tuple(current_points), current_boundary))

    missing_tail, completion_distance_m, completion_tolerance = _tail_quality(ordered, lifecycle, completion_point)
    quality = RouteSegmentationQuality(
        point_count=len(ordered),
        segment_count=len(segments),
        rejected_point_count=len(rejected),
        coverage_ratio=_coverage_ratio(ordered, lifecycle),
        max_gap_seconds=max_gap_seconds,
        missing_tail=missing_tail,
        completion_distance_m=completion_distance_m,
        completion_tolerance_m=completion_tolerance,
    )
    return SegmentedRoute(tuple(segments), tuple(rejected), quality)
