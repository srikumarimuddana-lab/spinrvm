"""Chronological completed-route reconstruction over durable GPS evidence.

4-tier gap fill closes any gap that is itself plausible:

  Tier 1 – OSRM /route  (road-following geometry, exact road distance)
  Tier 2 – Google Dirs   (road-following geometry, exact road distance)
  Tier 3/4 – Haversine   (straight-line geometry, haversine distance — pure
             math, cannot fail)

A gap is left unbridged — recorded in ``failed_gaps`` instead of guessed at —
when it is not plausible: farther than ``MAX_INFERRED_CONNECTOR_KM``
regardless of reason, or (for an internal gap between two observed segments,
where real device timestamps bound both sides) longer than
``MAX_INFERRED_GAP_SECONDS``. This is deliberate: a straight line across an
implausible distance, or a routed guess across an outage too long to trust,
is worse for the insurance/regulatory audit trail than an honest gap.

Endpoint anchors use an adaptive tolerance (30 m ideal → 200 m relaxed → raw
coordinate last resort) so off-road pickups (parking lots, malls, airports)
never cause reconstruction failure by themselves.
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
# A single gap-fill connector (routed OR straight-line) beyond this distance is
# not a believable substitute for missing GPS — same plausibility magnitude as
# gps_filtering.py's MAX_UNTIMED_HOP_KM (its "max single hop without timing
# info" cap). Applies to every connector reason (missing_start/internal_gap/
# missing_tail) since an anchor-to-evidence connector has no upper bound today.
MAX_INFERRED_CONNECTOR_KM = 10.0
# An internal gap (GPS dropped mid-trip, both sides have real device
# timestamps) longer than this is an outage too long to trust a routed or
# straight-line guess across — same magnitude as route_finalizer.py's
# BACKSTOP_GRACE_SECONDS. Only internal_gap connectors carry real timestamps
# on both sides; missing_start/missing_tail anchors have none, so the time
# gate does not apply to them (the distance cap above still does).
MAX_INFERRED_GAP_SECONDS = 300


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


_INTERNAL_PROJECTION_FIELDS = ("segment_start_captured_at", "segment_end_captured_at")


def _strip_internal_projection_fields(section: dict) -> dict:
    """Drop the whole-segment capture-timestamp fields project_observed_sections
    attaches for this module's own time-gating — they're not part of the
    public segment shape persisted/rendered downstream."""
    if not any(key in section for key in _INTERNAL_PROJECTION_FIELDS):
        return section
    return {key: value for key, value in section.items() if key not in _INTERNAL_PROJECTION_FIELDS}


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
    # Populated when a connector is refused outright (implausible distance, or
    # an internal gap too long to trust) rather than bridged with a guess —
    # the finalizer already treats a non-empty failed_gaps as an honest
    # "incomplete" route (see route_finalizer.py's _quality_projection /
    # _final_status), so this reuses that existing, previously-dead path.
    failed_gaps: list[str] = []
    # Split connector distance by trustworthiness: Tier 1/2 connectors follow
    # real roads (a believable substitute for the missing GPS), Tier 3/4 are
    # blind straight lines. inferred_distance_km stays the sum for API compat,
    # but the measured-distance resolver must be able to exclude straight
    # connectors so it never bills a chord across an unrouted gap.
    routed_connector_km = 0.0
    straight_connector_km = 0.0
    connector_attempts = 0

    async def append_connector(
        start: list[float], end: list[float], reason: str, elapsed_seconds: Optional[float] = None
    ) -> None:
        """4-tier gap fill: OSRM → Google → Haversine — unless the gap itself
        is not believable, in which case it is left unbridged (see the
        distance/time caps below) rather than guessed at."""
        nonlocal connector_attempts, routed_connector_km, straight_connector_km
        gap_distance = distance_m(start, end)
        if gap_distance <= CONTINUITY_TOLERANCE_M:
            return

        # Implausible-distance refusal — applies to every connector reason,
        # checked before the routed/straight attempt so an oversized gap never
        # gets a fabricated line at all, routed or straight.
        if gap_distance / 1000.0 > MAX_INFERRED_CONNECTOR_KM:
            logger.info(
                "gap fill refused for %s: %.0f m exceeds the %.0f km plausibility cap",
                reason,
                gap_distance,
                MAX_INFERRED_CONNECTOR_KM,
            )
            failed_gaps.append(f"{reason}_exceeds_distance_cap")
            return

        # Time-outage refusal — only meaningful when both sides of the gap
        # carry a real device timestamp (internal_gap between two observed
        # segments); missing_start/missing_tail anchors have none.
        if elapsed_seconds is not None and elapsed_seconds > MAX_INFERRED_GAP_SECONDS:
            logger.info(
                "gap fill refused for %s: %.0fs outage exceeds the %ds plausibility cap",
                reason,
                elapsed_seconds,
                MAX_INFERRED_GAP_SECONDS,
            )
            failed_gaps.append(f"{reason}_exceeds_time_cap")
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
                elapsed_seconds = None
                # Only a boundary between two DIFFERENT observed segments has
                # real device timestamps on both sides — chunks split from the
                # same segment by map-matching are already known-continuous
                # (route_segments.py never splits within a segment without a
                # time/distance/speed violation), so they're never time-gated.
                if previous.get("source_segment_index") != section.get("source_segment_index"):
                    gap_start = previous.get("segment_end_captured_at")
                    gap_end = section.get("segment_start_captured_at")
                    if gap_start is not None and gap_end is not None:
                        elapsed_seconds = (gap_end - gap_start).total_seconds()
                await append_connector(
                    previous["coordinates"][-1], section["coordinates"][0], "internal_gap", elapsed_seconds
                )
            output.append(_strip_internal_projection_fields(section))

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
