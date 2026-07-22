"""Pure projection of observed ride evidence into provenance-aware sections."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

try:
    from .route_segments import SegmentedRoute
except ImportError:
    from utils.route_segments import SegmentedRoute  # type: ignore


def coordinate(value: Any) -> Optional[list[float]]:
    if isinstance(value, dict):
        raw_lat = value.get("lat")
        raw_lng = value.get("lng")
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        raw_lat, raw_lng = value[0], value[1]
    else:
        return None
    try:
        lat = float(raw_lat)
        lng = float(raw_lng)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lng) or not -90 <= lat <= 90 or not -180 <= lng <= 180:
        return None
    return [round(lat, 6), round(lng, 6)]


def distance_m(left: list[float], right: list[float]) -> float:
    radius_m = 6_371_000.0
    lat1, lng1 = math.radians(left[0]), math.radians(left[1])
    lat2, lng2 = math.radians(right[0]), math.radians(right[1])
    delta_lat = lat2 - lat1
    delta_lng = lng2 - lng1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _polyline_distance_km(coordinates: list[list[float]]) -> float:
    return round(
        sum(distance_m(left, right) for left, right in zip(coordinates, coordinates[1:], strict=False)) / 1000.0,
        3,
    )


def project_observed_sections(segmented: SegmentedRoute, matched_route: Dict[str, Any]) -> list[dict]:
    """Project road-matched or raw observed sections without joining gaps."""
    failures_by_segment = {
        int(failure["segment_index"])
        for failure in (matched_route.get("failures") or [])
        if isinstance(failure, dict) and isinstance(failure.get("segment_index"), int)
    }
    matched_by_segment = {
        int(segment.get("segment_index", index)): segment
        for index, segment in enumerate(matched_route.get("segments") or [])
        if isinstance(segment, dict)
    }
    projected: list[dict] = []

    for observed_index, observed_segment in enumerate(segmented.observed_segments):
        matched_segment = matched_by_segment.get(observed_index)
        matchings = (
            matched_segment.get("matched_segments") or []
            if matched_segment and observed_index not in failures_by_segment
            else []
        )
        for matching_index, matching in enumerate(matchings):
            coordinates = [
                value for value in (coordinate(raw) for raw in (matching.get("polyline") or [])) if value is not None
            ]
            if len(coordinates) < 2:
                continue
            projected.append(
                {
                    "id": f"observed-{observed_index}-{matching_index}",
                    "source_segment_index": observed_index,
                    "chunk_index": matching.get("chunk_index"),
                    "provider": matching.get("provider") or "osrm_match",
                    "geometry_kind": "observed",
                    "gap_reason": None,
                    "distance_km": round(float(matching.get("distance_km") or 0), 3),
                    "coordinates": coordinates,
                }
            )
        if matchings and any(section["source_segment_index"] == observed_index for section in projected):
            continue

        coordinates = [value for value in (coordinate(point) for point in observed_segment.points) if value is not None]
        if len(coordinates) < 2:
            continue
        projected.append(
            {
                "id": f"observed-{observed_index}-fallback",
                "source_segment_index": observed_index,
                "provider": "observed_fallback",
                "geometry_kind": "observed",
                "gap_reason": None,
                "distance_km": _polyline_distance_km(coordinates),
                "coordinates": coordinates,
            }
        )
    return projected
