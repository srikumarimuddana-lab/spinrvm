"""Chronological completed-route reconstruction over durable GPS evidence."""

from __future__ import annotations

from typing import Any, Dict

try:
    from ..core.config import settings
    from ..settings_loader import get_app_settings
except ImportError:
    from core.config import settings  # type: ignore
    from settings_loader import get_app_settings  # type: ignore

try:
    from .route_distance import compute_gap_route_via_osrm, snap_endpoint_via_osrm
    from .route_reconstruction_projection import coordinate, distance_m, project_observed_sections
    from .route_segments import SegmentedRoute
except ImportError:
    from utils.route_distance import compute_gap_route_via_osrm, snap_endpoint_via_osrm  # type: ignore
    from utils.route_reconstruction_projection import (  # type: ignore
        coordinate,
        distance_m,
        project_observed_sections,
    )
    from utils.route_segments import SegmentedRoute  # type: ignore


CONTINUITY_TOLERANCE_M = 30.0
MAX_INFERRED_CONNECTORS = 20


async def reconstruct_completed_route(
    segmented: SegmentedRoute,
    matched_route: Dict[str, Any],
    pickup_point: Dict[str, Any],
    completion_point: Dict[str, Any],
) -> dict:
    """Insert bounded OSRM Route sections between ordered observed evidence."""
    observed_sections = project_observed_sections(segmented, matched_route)
    app_settings = await get_app_settings() or {}
    osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    pickup = coordinate(pickup_point)
    completion = coordinate(completion_point)

    first_coordinate = observed_sections[0]["coordinates"][0] if observed_sections else None
    last_coordinate = observed_sections[-1]["coordinates"][-1] if observed_sections else None
    snapped_pickup = await snap_endpoint_via_osrm(pickup_point, osrm_url) if pickup and osrm_url else None
    snapped_completion = await snap_endpoint_via_osrm(completion_point, osrm_url) if completion and osrm_url else None

    start_anchor = snapped_pickup
    if (
        start_anchor is None
        and pickup
        and first_coordinate
        and distance_m(pickup, first_coordinate) <= CONTINUITY_TOLERANCE_M
    ):
        start_anchor = pickup
    end_anchor = snapped_completion
    if (
        end_anchor is None
        and completion
        and last_coordinate
        and distance_m(last_coordinate, completion) <= CONTINUITY_TOLERANCE_M
    ):
        end_anchor = completion

    output: list[dict] = []
    failed_gaps: list[str] = []
    inferred_distance_km = 0.0
    connector_attempts = 0

    async def append_connector(start: list[float], end: list[float], reason: str) -> None:
        nonlocal connector_attempts, inferred_distance_km
        if distance_m(start, end) <= CONTINUITY_TOLERANCE_M:
            return
        connector_attempts += 1
        if not osrm_url or connector_attempts > MAX_INFERRED_CONNECTORS:
            failed_gaps.append(reason)
            return
        routed = await compute_gap_route_via_osrm(start, end, osrm_url)
        if not routed:
            failed_gaps.append(reason)
            return
        distance_km, coordinates = routed
        output.append(
            {
                "id": f"inferred-{reason}-{connector_attempts}",
                "provider": "osrm_inferred",
                "geometry_kind": "inferred",
                "gap_reason": reason,
                "distance_km": round(float(distance_km), 3),
                "coordinates": coordinates,
            }
        )
        inferred_distance_km += float(distance_km)

    if observed_sections:
        if start_anchor is None:
            failed_gaps.append("missing_start")
        else:
            await append_connector(start_anchor, observed_sections[0]["coordinates"][0], "missing_start")

        for index, section in enumerate(observed_sections):
            if index > 0:
                previous = observed_sections[index - 1]
                await append_connector(previous["coordinates"][-1], section["coordinates"][0], "internal_gap")
            output.append(section)

        if end_anchor is None:
            failed_gaps.append("missing_tail")
        else:
            await append_connector(observed_sections[-1]["coordinates"][-1], end_anchor, "missing_tail")
    else:
        if start_anchor is not None and end_anchor is not None:
            await append_connector(start_anchor, end_anchor, "missing_start")
        else:
            failed_gaps.extend(["missing_start", "missing_tail"])

    observed_distance_km = round(sum(float(section.get("distance_km") or 0) for section in observed_sections), 3)
    inferred_distance_km = round(inferred_distance_km, 3)
    total_distance_km = round(observed_distance_km + inferred_distance_km, 3)
    observed_ratio = round(observed_distance_km / total_distance_km, 3) if total_distance_km > 0 else 0.0
    inferred_ratio = round(inferred_distance_km / total_distance_km, 3) if total_distance_km > 0 else 0.0

    return {
        "segments": output,
        "distance_km": total_distance_km,
        "observed_distance_km": observed_distance_km,
        "inferred_distance_km": inferred_distance_km,
        "observed_distance_ratio": observed_ratio,
        "inferred_distance_ratio": inferred_ratio,
        "inferred_gap_count": sum(1 for section in output if section.get("geometry_kind") == "inferred"),
        "endpoint_start_verified": start_anchor is not None,
        "endpoint_end_verified": end_anchor is not None,
        "failed_gaps": failed_gaps,
    }
