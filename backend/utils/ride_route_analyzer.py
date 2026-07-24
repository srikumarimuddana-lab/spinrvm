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
    from .gps_filtering import (
        MAX_TRUSTED_ACCURACY_M,
        collapse_stationary_clusters,
        filter_low_accuracy,
    )
    from .route_segments import (
        MAX_CONTINUOUS_GAP_SECONDS,
        MAX_PLAUSIBLE_SPEED_KPH,
        SegmentedRoute,
        segment_route,
    )
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.gps_filtering import (  # type: ignore
        MAX_TRUSTED_ACCURACY_M,
        collapse_stationary_clusters,
        filter_low_accuracy,
    )
    from utils.route_segments import (  # type: ignore
        MAX_CONTINUOUS_GAP_SECONDS,
        MAX_PLAUSIBLE_SPEED_KPH,
        SegmentedRoute,
        segment_route,
    )


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


@dataclass(frozen=True)
class RouteProjection:
    """Sanitized provider metrics plus optional local coordinate geometry."""

    report: Dict[str, Any]
    geojson: Dict[str, Any] | None


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


def _geojson_lines(reconstructed: Dict[str, Any]) -> list[list[list[float]]]:
    lines: list[list[list[float]]] = []
    for section in reconstructed.get("segments") or []:
        if not isinstance(section, dict):
            continue
        coordinates = section.get("coordinates") or []
        line: list[list[float]] = []
        for coordinate in coordinates:
            if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
                continue
            try:
                lat, lng = float(coordinate[0]), float(coordinate[1])
            except (TypeError, ValueError):
                continue
            point = [lng, lat]
            if not line or line[-1] != point:
                line.append(point)
        if len(line) >= 2:
            lines.append(line)
    return lines


async def _compute_segmented_route(observed_segments: list[Any]) -> Dict[str, Any]:
    """Load the configured provider stack only when projection is requested."""

    try:
        from .route_distance import compute_segmented_road_route
    except ImportError:
        from utils.route_distance import compute_segmented_road_route  # type: ignore
    return await compute_segmented_road_route(observed_segments)


async def _reconstruct_route(
    segmented: SegmentedRoute,
    matched: Dict[str, Any],
    pickup: Dict[str, Any],
    completion: Dict[str, Any],
) -> Dict[str, Any]:
    """Load route reconstruction lazily so offline evidence analysis stays pure."""

    try:
        from .route_reconstruction import reconstruct_completed_route
    except ImportError:
        from utils.route_reconstruction import reconstruct_completed_route  # type: ignore
    return await reconstruct_completed_route(segmented, matched, pickup, completion)


async def project_phase_3_route(
    analysis: RideRouteAnalysis,
    ride: Dict[str, Any],
) -> RouteProjection:
    """Map-match and reconstruct only timestamp-derived Phase 3 evidence.

    Each returned section remains a separate member of one MultiLineString, so
    an unresolved gap is never rendered as a straight chord.  The single
    feature carries one uniform actual-route style marker.
    """

    matched = await _compute_segmented_route(list(analysis.segmented_phase_3.observed_segments))
    pickup = {"lat": ride.get("pickup_lat"), "lng": ride.get("pickup_lng")}
    completion = {"lat": ride.get("dropoff_lat"), "lng": ride.get("dropoff_lng")}
    reconstructed = await _reconstruct_route(
        analysis.segmented_phase_3,
        matched,
        pickup,
        completion,
    )
    lines = _geojson_lines(reconstructed)
    failed_gaps = list(reconstructed.get("failed_gaps") or [])
    endpoints_verified = (
        reconstructed.get("endpoint_start_verified") is True and reconstructed.get("endpoint_end_verified") is True
    )
    provider = matched.get("provider")
    unavailable = provider is None and all(
        isinstance(failure, dict) and failure.get("reason") == "provider_unavailable"
        for failure in (matched.get("failures") or [])
    )
    if unavailable and not lines:
        status = "unavailable"
    elif lines and not failed_gaps and endpoints_verified:
        status = "complete"
    else:
        status = "incomplete"

    report = {
        "osrm_status": status,
        "distance_provider": provider,
        "osrm_distance_km": reconstructed.get("distance_km"),
        "observed_distance_km": reconstructed.get("observed_distance_km"),
        "inferred_distance_km": reconstructed.get("inferred_distance_km"),
        "observed_distance_ratio": reconstructed.get("observed_distance_ratio"),
        "inferred_distance_ratio": reconstructed.get("inferred_distance_ratio"),
        "inferred_gap_count": int(reconstructed.get("inferred_gap_count") or 0),
        "unresolved_gap_count": len(failed_gaps),
        "endpoint_start_verified": reconstructed.get("endpoint_start_verified") is True,
        "endpoint_end_verified": reconstructed.get("endpoint_end_verified") is True,
    }
    geojson = None
    if lines:
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"route_kind": "actual", "style": "uniform-solid"},
                    "geometry": {"type": "MultiLineString", "coordinates": lines},
                }
            ],
        }
    return RouteProjection(report, geojson)


def _haversine_km(points: list[Dict[str, Any]]) -> float:
    """Time-ordered raw haversine sum over a point list (km)."""
    total = 0.0
    for left, right in zip(points, points[1:], strict=False):
        try:
            total += calculate_distance(
                float(left["lat"]), float(left["lng"]), float(right["lat"]), float(right["lng"])
            )
        except (KeyError, TypeError, ValueError):
            continue
    return round(total, 3)


def _accuracy_histogram(points: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """p50/p90/max reported accuracy and how many fixes exceed the trust gate."""
    values: list[float] = []
    null_count = 0
    for point in points:
        raw = point.get("accuracy")
        if raw is None:
            null_count += 1
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            null_count += 1
    if not values:
        return {"count": 0, "null_count": null_count, "p50_m": None, "p90_m": None, "max_m": None, "over_gate_count": 0}
    values.sort()

    def _pct(q: float) -> float:
        return round(values[min(len(values) - 1, int(q * len(values)))], 1)

    return {
        "count": len(values),
        "null_count": null_count,
        "p50_m": _pct(0.5),
        "p90_m": _pct(0.9),
        "max_m": round(values[-1], 1),
        "over_gate_count": sum(1 for v in values if v > MAX_TRUSTED_ACCURACY_M),
        "accuracy_gate_m": MAX_TRUSTED_ACCURACY_M,
    }


def _phase3_raw_points(ride: Dict[str, Any], points: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """All in-window Phase 3 fixes, time-ordered (pre-rejection)."""
    requested_at, started_at, completed_at = _boundaries(ride)
    bucket: list[tuple[datetime, Dict[str, Any]]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        captured_at = parse_iso_utc(point.get("captured_at") or point.get("timestamp"))
        if captured_at is None:
            continue
        if _phase_for(captured_at, requested_at, started_at, completed_at) == "phase_3":
            bucket.append((captured_at, point))
    bucket.sort(key=lambda item: item[0])
    return [point for _, point in bucket]


def _gap_timeline(
    phase3_sorted: list[Dict[str, Any]], gap_events: Iterable[Dict[str, Any]] | None
) -> list[Dict[str, Any]]:
    """Dead zones from breadcrumb deltas union recorded gap events (no coordinates)."""
    gaps: list[Dict[str, Any]] = []
    for left, right in zip(phase3_sorted, phase3_sorted[1:], strict=False):
        t_left = parse_iso_utc(left.get("captured_at") or left.get("timestamp"))
        t_right = parse_iso_utc(right.get("captured_at") or right.get("timestamp"))
        if t_left is None or t_right is None:
            continue
        seconds = (t_right - t_left).total_seconds()
        if seconds > MAX_CONTINUOUS_GAP_SECONDS:
            gaps.append(
                {
                    "from": t_left.isoformat(),
                    "to": t_right.isoformat(),
                    "gap_seconds": int(seconds),
                    "source": "breadcrumb_delta",
                }
            )
    for event in gap_events or []:
        if not isinstance(event, dict):
            continue
        started = event.get("gap_started_at") or event.get("started_at") or event.get("opened_at")
        resolved = event.get("gap_resolved_at") or event.get("resolved_at") or event.get("closed_at")
        gaps.append(
            {
                "from": started,
                "to": resolved,
                "gap_seconds": event.get("gap_seconds") or event.get("duration_seconds"),
                "source": "gap_event",
                "resolved": resolved is not None,
            }
        )
    return gaps


def _implied_kmh(prev: Dict[str, Any], curr: Dict[str, Any]) -> tuple[float, float | None, float | None]:
    """Return ``(jump_metres, elapsed_seconds, implied_kmh)`` between two fixes.

    ``elapsed``/``implied_kmh`` are None when a timestamp is missing; implied
    speed is ``inf`` when two fixes share a timestamp but moved (a hard spike).
    """
    try:
        jump_m = (
            calculate_distance(float(prev["lat"]), float(prev["lng"]), float(curr["lat"]), float(curr["lng"])) * 1000.0
        )
    except (KeyError, TypeError, ValueError):
        return 0.0, None, None
    t_prev = parse_iso_utc(prev.get("captured_at") or prev.get("timestamp"))
    t_curr = parse_iso_utc(curr.get("captured_at") or curr.get("timestamp"))
    if t_prev is None or t_curr is None:
        return jump_m, None, None
    elapsed = (t_curr - t_prev).total_seconds()
    if elapsed <= 0:
        return jump_m, elapsed, (float("inf") if jump_m > 0 else 0.0)
    return jump_m, elapsed, jump_m / elapsed * 3.6


def build_outlier_table(
    ordered_points: list[Dict[str, Any]],
    *,
    max_kmh: float = MAX_PLAUSIBLE_SPEED_KPH,
) -> Dict[str, Any]:
    """Per-fix trajectory + spike-outlier verdict for a time-ordered trace.

    Mirrors the pipeline's despike exactly (measure against the last KEPT fix;
    an into-jump above ``max_kmh`` is a ``spike_outlier`` iff skipping the fix
    reconnects its neighbours below ``max_kmh``, otherwise ``outage_or_gap``),
    so the table names precisely which fixes the route pipeline drops and why.
    Coordinate-free: rows carry only index/time/jump/speed/verdict — never a
    lat/lng — so the report stays PIPEDA-safe.
    """
    rows: list[Dict[str, Any]] = []
    outlier_count = 0
    if not ordered_points:
        return {"outlier_count": 0, "max_plausible_kmh": max_kmh, "rows": rows}

    def _row(index: int, point: Dict[str, Any], jump_m, elapsed, kmh, verdict) -> Dict[str, Any]:
        return {
            "index": index,
            "timestamp": str(point.get("captured_at") or point.get("timestamp") or ""),
            "jump_m": round(float(jump_m), 1) if jump_m is not None else None,
            "elapsed_s": round(float(elapsed), 3) if elapsed is not None else None,
            "implied_kmh": (None if kmh is None else (None if kmh == float("inf") else round(float(kmh), 1))),
            "implied_speed_impossible": bool(kmh is not None and kmh > max_kmh),
            "verdict": verdict,
        }

    rows.append(_row(0, ordered_points[0], 0.0, None, None, "start"))
    anchor = ordered_points[0]
    for index in range(1, len(ordered_points)):
        current = ordered_points[index]
        following = ordered_points[index + 1] if index + 1 < len(ordered_points) else None
        jump_m, elapsed, kmh = _implied_kmh(anchor, current)
        verdict = "ok"
        if kmh is not None and kmh > max_kmh:
            skip_ok = False
            if following is not None:
                _sj, _se, skip_kmh = _implied_kmh(anchor, following)
                skip_ok = skip_kmh is not None and skip_kmh <= max_kmh
            if skip_ok:
                verdict = "spike_outlier"
                outlier_count += 1
            else:
                verdict = "outage_or_gap"
        rows.append(_row(index, current, jump_m, elapsed, kmh, verdict))
        # Advance the anchor only past a KEPT fix — a dropped spike must not
        # become the reference for the next fix (matches _despike).
        if verdict != "spike_outlier":
            anchor = current
    return {"outlier_count": outlier_count, "max_plausible_kmh": max_kmh, "rows": rows}


def build_incident_report(
    ride: Dict[str, Any],
    points: Iterable[Dict[str, Any]],
    *,
    gap_events: Iterable[Dict[str, Any]] | None = None,
    recomputes: Iterable[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """A coordinate-free phase-by-phase incident report for one ride.

    Composes the phase breakdown (via analyze_ride_evidence), an accuracy
    histogram, the effect of the GPS filter (dropped / stationary-collapsed), a
    gap timeline, and the full DISTANCE LADDER - booked / straight-line /
    raw-GPS / filtered-GPS / settled / stored - so the divergence that produced
    a wrong number is legible at a glance. PIPEDA-safe: no coordinates leave the
    analysis. ``points`` may be a one-shot iterable; it is materialised once.
    """
    points = list(points)
    analysis = analyze_ride_evidence(ride, points)

    phase3 = _phase3_raw_points(ride, points)
    raw_km = _haversine_km(phase3)
    kept_accuracy, dropped_low_accuracy, null_accuracy = filter_low_accuracy(phase3)
    filtered_points, collapsed_stationary = collapse_stationary_clusters(kept_accuracy)
    filtered_km = _haversine_km(filtered_points)

    try:
        straight_line_km: float | None = round(
            calculate_distance(
                float(ride["pickup_lat"]),
                float(ride["pickup_lng"]),
                float(ride["dropoff_lat"]),
                float(ride["dropoff_lng"]),
            ),
            3,
        )
    except (KeyError, TypeError, ValueError):
        straight_line_km = None

    trip_metrics = ((ride.get("ride_metrics") or {}).get("phases") or {}).get("trip_in_progress") or {}

    def _f(value: Any) -> float | None:
        try:
            return round(float(value), 3)
        except (TypeError, ValueError):
            return None

    distance_ladder = {
        "booked_planned_km": _f(ride.get("planned_distance_km")),
        "straight_line_pickup_dropoff_km": straight_line_km,
        "raw_gps_phase3_km": raw_km,
        "filtered_gps_phase3_km": filtered_km,
        "metrics_actual_km": _f(trip_metrics.get("actual_distance_km")),
        "metrics_road_snapped_km": _f(trip_metrics.get("actual_distance_km_road_snapped")),
        "settled_distance_km": _f(ride.get("distance_km")),
        "stored_actual_distance_km": _f(ride.get("actual_distance_km")),
        "measured_distance_basis": trip_metrics.get("distance_basis"),
    }

    fare_summary = {
        "total_fare": ride.get("total_fare"),
        "planned_distance_km": _f(ride.get("planned_distance_km")),
        "booking_distance_basis": ride.get("distance_basis"),
    }

    return {
        **analysis.report,
        "incident_report": True,
        "distance_ladder": distance_ladder,
        "accuracy_histogram_phase3": _accuracy_histogram(phase3),
        "gps_filter_effect_phase3": {
            "raw_point_count": len(phase3),
            "dropped_low_accuracy": dropped_low_accuracy,
            "null_accuracy_points": null_accuracy,
            "collapsed_stationary": collapsed_stationary,
            "raw_km": raw_km,
            "filtered_km": filtered_km,
            "filtered_delta_km": round(raw_km - filtered_km, 3),
        },
        "gap_timeline_phase3": _gap_timeline(phase3, gap_events),
        "trajectory_outliers_phase3": build_outlier_table(phase3),
        "fare_summary": fare_summary,
        "distance_recompute_audit": [
            {
                "route_revision": r.get("route_revision"),
                "previous_actual_distance_km": r.get("previous_actual_distance_km"),
                "new_actual_distance_km": r.get("new_actual_distance_km"),
                "trigger": r.get("trigger"),
            }
            for r in (recomputes or [])
            if isinstance(r, dict)
        ],
    }
