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

try:
    from .gps_filtering import MAX_TRUSTED_ACCURACY_M
except ImportError:
    from utils.gps_filtering import MAX_TRUSTED_ACCURACY_M  # type: ignore


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
    # First→last observed fraction (pre-gap-aware). coverage_ratio is now the
    # gap-aware value; this preserves the old figure for continuity.
    span_coverage_ratio: float = 0.0


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
        # Drop fixes too imprecise to anchor geometry: map-matching a >50 m fix
        # snaps it to whatever street is nearest, which is how the incident's
        # parking-lot jitter became rendered loops. Missing/non-numeric accuracy
        # is kept (can't prove it's bad) — same policy as gps_filtering.
        accuracy = point.get("accuracy")
        if accuracy is not None:
            try:
                accuracy_m: float | None = float(accuracy)
            except (TypeError, ValueError):
                accuracy_m = None
            if accuracy_m is not None and accuracy_m > MAX_TRUSTED_ACCURACY_M:
                rejected.append(_reject(point, "low_accuracy"))
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
    # Despike AFTER final ordering so an isolated stale/duplicate fix (e.g. a
    # cached pickup coordinate re-sent mid-trip) is dropped rather than kept as
    # the start vertex of the next segment — otherwise it renders as the route
    # "going back". The offending points are still preserved in raw breadcrumbs.
    accepted, spike_rejected = _despike(accepted)
    rejected.extend(spike_rejected)
    return accepted, rejected


def _transition_impossible(previous: _ParsedPoint, current: _ParsedPoint) -> bool:
    """True when previous→current implies a physically impossible ground speed.

    Mirrors the speed guard in ``_boundary_reason`` but returns a plain bool for
    the despike pass.

    A legacy fix (no ``recording_session_id``/``sequence_number``) still carries
    a real device ``timestamp`` that ``_parse_points`` uses as ``captured_at``,
    so a POSITIVE elapsed between two legacy fixes is a genuine device-time delta
    and its implied speed is meaningful — an isolated teleport-and-return spike
    (a stale/duplicate coordinate re-sent mid-trip) reads as thousands of km/h
    and must be despiked, or it inflates the measured distance and draws a
    "repeated" backtrack on the map. Only when elapsed is non-positive is legacy
    timing truly meaningless (server-receive-time batches assigned in one insert
    loop); there we defer to the segmentation boundary logic rather than guess.

    The despike's two-sided test keeps this safe for real zero-cadence batches:
    when every pair in a batch reads impossible, skipping one point never
    reconnects the neighbours plausibly, so nothing is dropped.
    """
    elapsed_seconds = (current.captured_at - previous.captured_at).total_seconds()
    displacement_meters = _distance_meters(previous.point, current.point)
    if elapsed_seconds <= 0:
        # Non-legacy: a zero delta with real displacement is itself impossible.
        # Legacy: elapsed is meaningless here — cannot judge, so do not despike.
        if previous.is_legacy and current.is_legacy:
            return False
        return displacement_meters > 0
    return (displacement_meters / elapsed_seconds * 3.6) > MAX_PLAUSIBLE_SPEED_KPH


def _despike(ordered: List[_ParsedPoint]) -> tuple[List[_ParsedPoint], List[RejectedRoutePoint]]:
    """Drop isolated spike fixes so they never become a drawn map vertex.

    A point is an isolated spike when the transition INTO it implies an
    impossible speed, yet skipping it reconnects its neighbours at a plausible
    speed — the signature of a stale/duplicate GPS fix (a cached pickup
    coordinate re-sent mid-trip, a momentary teleport-and-return). It is dropped
    so the rendered route no longer jumps back to it. A genuine outage (both
    sides impossible — a real teleport) is left for the segmentation boundary
    logic, which correctly splits the trace there.
    """
    if len(ordered) < 3:
        return ordered, []
    kept: List[_ParsedPoint] = [ordered[0]]
    dropped: List[RejectedRoutePoint] = []
    index = 1
    while index < len(ordered):
        current = ordered[index]
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        previous = kept[-1]
        if (
            following is not None
            and _transition_impossible(previous, current)
            and not _transition_impossible(previous, following)
        ):
            dropped.append(_reject(current.point, "spike_outlier"))
            index += 1
            continue
        kept.append(current)
        index += 1
    return kept, dropped


def _boundary_reason(previous: _ParsedPoint, current: _ParsedPoint) -> tuple[str | None, int]:
    if previous.recording_session_id != current.recording_session_id:
        return "session_boundary", max(0, int((current.captured_at - previous.captured_at).total_seconds()))

    elapsed_seconds = max(0, int((current.captured_at - previous.captured_at).total_seconds()))
    # Insurance-period boundary: never draw one line across a phase change
    # (P2 pickup leg → P3 passenger trip). Inert for P3-only inputs — every
    # point carries the same tracking_phase there. Only splits when BOTH
    # points are tagged; untagged legacy rows keep today's behaviour.
    previous_phase = previous.point.get("tracking_phase")
    current_phase = current.point.get("tracking_phase")
    if previous_phase and current_phase and previous_phase != current_phase:
        return "phase_change", elapsed_seconds
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


def _coverage_ratios(points: List[_ParsedPoint], lifecycle: Dict[str, Any]) -> tuple[float, float]:
    """Return ``(gap_aware_ratio, span_ratio)`` coverage of the trip window.

    ``span_ratio`` is the historical first→last observed fraction of the trip.
    ``gap_aware_ratio`` additionally subtracts internal dead zones longer than
    ``MAX_CONTINUOUS_GAP_SECONDS`` — so a mid-trip tracking dropout no longer
    reads as near-complete coverage just because fixes exist at both ends (the
    "87% observed" that hid the incident's missing middle). Both are 0.0 when
    the lifecycle timestamps are unusable.
    """
    if not points:
        return 0.0, 0.0
    started_at = parse_iso_utc(lifecycle.get("ride_started_at") or lifecycle.get("started_at"))
    completed_at = parse_iso_utc(lifecycle.get("ride_completed_at") or lifecycle.get("completed_at"))
    if started_at is None or completed_at is None or completed_at <= started_at:
        return 0.0, 0.0
    total_seconds = (completed_at - started_at).total_seconds()
    first = max(points[0].captured_at, started_at)
    last = min(points[-1].captured_at, completed_at)
    if last <= first:
        return 0.0, 0.0
    span_seconds = (last - first).total_seconds()
    span_ratio = round(min(1.0, span_seconds / total_seconds), 3)
    # Subtract each internal gap that exceeds the continuity threshold — the
    # span itself already excludes lead-in/tail (first>started / last<completed).
    covered_seconds = span_seconds
    for prev_point, next_point in zip(points, points[1:], strict=False):
        gap_seconds = (next_point.captured_at - prev_point.captured_at).total_seconds()
        if gap_seconds > MAX_CONTINUOUS_GAP_SECONDS:
            covered_seconds -= gap_seconds
    gap_aware_ratio = round(max(0.0, min(1.0, covered_seconds / total_seconds)), 3)
    return gap_aware_ratio, span_ratio


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
    gap_aware_coverage, span_coverage = _coverage_ratios(ordered, lifecycle)
    quality = RouteSegmentationQuality(
        point_count=len(ordered),
        segment_count=len(segments),
        rejected_point_count=len(rejected),
        coverage_ratio=gap_aware_coverage,
        max_gap_seconds=max_gap_seconds,
        missing_tail=missing_tail,
        completion_distance_m=completion_distance_m,
        completion_tolerance_m=completion_tolerance,
        span_coverage_ratio=span_coverage,
    )
    return SegmentedRoute(tuple(segments), tuple(rejected), quality)
