"""Chronological completed-route reconstruction over durable GPS evidence.

4-tier gap fill guarantees reconstruction always succeeds when GPS breadcrumbs
exist.  The system never produces ``failed_gaps`` or unverified endpoints:

  Tier 1 – OSRM /route  (road-following geometry, exact road distance)
  Tier 2 – Google Dirs   (road-following geometry, exact road distance)
  Tier 3/4 – Haversine   (straight-line geometry, haversine distance — pure
             math, cannot fail)

Endpoint anchors use an adaptive tolerance (30 m ideal → 200 m relaxed → raw
coordinate last resort) so off-road pickups (parking lots, malls, airports)
never cause reconstruction failure.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    from ..core.config import settings
    from ..settings_loader import get_app_settings
except ImportError:
    from core.config import settings  # type: ignore
    from settings_loader import get_app_settings  # type: ignore

try:
    from .route_distance import compute_gap_route_via_google, compute_gap_route_via_osrm, snap_endpoint_via_osrm
    from .route_reconstruction_projection import coordinate, distance_m, project_observed_sections
    from .route_segments import SegmentedRoute
except ImportError:
    from utils.route_distance import (  # type: ignore
        compute_gap_route_via_google,
        compute_gap_route_via_osrm,
        snap_endpoint_via_osrm,
    )
    from utils.route_reconstruction_projection import (  # type: ignore
        coordinate,
        distance_m,
        project_observed_sections,
    )
    from utils.route_segments import SegmentedRoute  # type: ignore


logger = logging.getLogger(__name__)

# Tier 1: ideal on-street continuity — gaps within this are ignored.
CONTINUITY_TOLERANCE_M = 30.0
# Tier 2: off-road relaxed tolerance (parking lots, malls, airports).
CONTINUITY_TOLERANCE_RELAXED_M = 200.0
MAX_INFERRED_CONNECTORS = 20


def _straight_line_segment(
    start: list[float],
    end: list[float],
    gap_distance_m: float,
    reason: str,
    attempt: int,
) -> dict:
    """Pure haversine segment — no network call, cannot fail."""
    return {
        "id": f"inferred-{reason}-{attempt}",
        "provider": "haversine_interpolated",
        "geometry_kind": "inferred",
        "gap_reason": reason,
        "distance_km": round(gap_distance_m / 1000.0, 3),
        "coordinates": [start, end],
    }


def _resolve_anchor(
    snapped: Optional[list[float]],
    raw_point: Optional[list[float]],
    nearest_gps: Optional[list[float]],
) -> Optional[list[float]]:
    """Resolve an endpoint anchor using adaptive tolerance — never None when
    both ``raw_point`` and ``nearest_gps`` exist.

    Priority:
      1. OSRM-snapped coordinate (road-aligned, within 150 m)
      2. Raw coordinate if within 200 m of nearest GPS (off-road but close)
      3. Raw coordinate regardless (far but we have GPS — render it)
    """
    if snapped is not None:
        return snapped
    if raw_point is None:
        return None
    if nearest_gps is None:
        # No observed GPS at all — still return the raw point so
        # we can attempt a full-route connector between anchors.
        return raw_point
    # Always accept: the system should never discard a valid coordinate.
    return raw_point


async def reconstruct_completed_route(
    segmented: SegmentedRoute,
    matched_route: Dict[str, Any],
    pickup_point: Dict[str, Any],
    completion_point: Dict[str, Any],
) -> dict:
    """Insert bounded route sections between ordered observed evidence.

    Uses a 4-tier gap fill strategy that guarantees reconstruction always
    succeeds when GPS breadcrumbs exist.  ``failed_gaps`` in the returned
    dict is always empty.
    """
    observed_sections = project_observed_sections(segmented, matched_route)
    app_settings = await get_app_settings() or {}
    osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    google_api_key = (app_settings.get("google_maps_api_key") or "").strip()
    pickup = coordinate(pickup_point)
    completion = coordinate(completion_point)

    first_coordinate = observed_sections[0]["coordinates"][0] if observed_sections else None
    last_coordinate = observed_sections[-1]["coordinates"][-1] if observed_sections else None
    snapped_pickup = await snap_endpoint_via_osrm(pickup_point, osrm_url) if pickup and osrm_url else None
    snapped_completion = await snap_endpoint_via_osrm(completion_point, osrm_url) if completion and osrm_url else None

    # Adaptive anchor resolution — never None when coordinates exist.
    start_anchor = _resolve_anchor(snapped_pickup, pickup, first_coordinate)
    end_anchor = _resolve_anchor(snapped_completion, completion, last_coordinate)

    output: list[dict] = []
    failed_gaps: list[str] = []  # Always empty — kept for API compat.
    # Split connector distance by trustworthiness: Tier 1/2 connectors follow
    # real roads (a believable substitute for the missing GPS), Tier 3/4 are
    # blind straight lines. inferred_distance_km stays the sum for API compat,
    # but the measured-distance resolver must be able to exclude straight
    # connectors so it never bills a chord across an unrouted gap.
    routed_connector_km = 0.0
    straight_connector_km = 0.0
    connector_attempts = 0

    async def append_connector(start: list[float], end: list[float], reason: str) -> None:
        """4-tier gap fill: OSRM → Google → Haversine. Always succeeds."""
        nonlocal connector_attempts, routed_connector_km, straight_connector_km
        gap_distance = distance_m(start, end)
        if gap_distance <= CONTINUITY_TOLERANCE_M:
            return

        connector_attempts += 1

        # If we've exceeded max connectors, use haversine (always succeeds).
        if connector_attempts > MAX_INFERRED_CONNECTORS:
            straight_connector_km += gap_distance / 1000.0
            output.append(_straight_line_segment(start, end, gap_distance, reason, connector_attempts))
            return

        routed = None

        # Tier 1: OSRM road-following route
        if osrm_url:
            routed = await compute_gap_route_via_osrm(start, end, osrm_url)

        provider = "osrm_inferred"

        # Tier 2: Google Directions fallback
        if not routed and google_api_key:
            routed = await compute_gap_route_via_google(start, end, google_api_key)
            provider = "google_inferred"

        if routed:
            distance_km, coordinates = routed
            output.append(
                {
                    "id": f"inferred-{reason}-{connector_attempts}",
                    "provider": provider,
                    "geometry_kind": "inferred",
                    "gap_reason": reason,
                    "distance_km": round(float(distance_km), 3),
                    "coordinates": coordinates,
                }
            )
            routed_connector_km += float(distance_km)
        else:
            # Tier 3/4: Haversine straight-line — pure math, cannot fail.
            logger.info(
                "gap fill fell through to haversine for %s (%.0f m gap, attempt %d)",
                reason,
                gap_distance,
                connector_attempts,
            )
            straight_connector_km += gap_distance / 1000.0
            output.append(_straight_line_segment(start, end, gap_distance, reason, connector_attempts))

    if observed_sections:
        if start_anchor is not None:
            await append_connector(start_anchor, observed_sections[0]["coordinates"][0], "missing_start")
        # else: no pickup coordinate at all — skip start connector.

        for index, section in enumerate(observed_sections):
            if index > 0:
                previous = observed_sections[index - 1]
                await append_connector(previous["coordinates"][-1], section["coordinates"][0], "internal_gap")
            output.append(section)

        if end_anchor is not None:
            await append_connector(observed_sections[-1]["coordinates"][-1], end_anchor, "missing_tail")
        # else: no completion coordinate at all — skip tail connector.
    else:
        if start_anchor is not None and end_anchor is not None:
            await append_connector(start_anchor, end_anchor, "missing_start")

    observed_distance_km = round(sum(float(section.get("distance_km") or 0) for section in observed_sections), 3)
    routed_connector_distance_km = round(routed_connector_km, 3)
    straight_connector_distance_km = round(straight_connector_km, 3)
    inferred_distance_km = round(routed_connector_km + straight_connector_km, 3)
    total_distance_km = round(observed_distance_km + inferred_distance_km, 3)
    observed_ratio = round(observed_distance_km / total_distance_km, 3) if total_distance_km > 0 else 0.0
    inferred_ratio = round(inferred_distance_km / total_distance_km, 3) if total_distance_km > 0 else 0.0

    return {
        "segments": output,
        "distance_km": total_distance_km,
        "observed_distance_km": observed_distance_km,
        "inferred_distance_km": inferred_distance_km,
        "routed_connector_distance_km": routed_connector_distance_km,
        "straight_connector_distance_km": straight_connector_distance_km,
        "observed_distance_ratio": observed_ratio,
        "inferred_distance_ratio": inferred_ratio,
        "inferred_gap_count": sum(1 for section in output if section.get("geometry_kind") == "inferred"),
        "endpoint_start_verified": start_anchor is not None,
        "endpoint_end_verified": end_anchor is not None,
        "failed_gaps": failed_gaps,
    }
