"""Chronological completed-route reconstruction over durable GPS evidence.

4-tier gap fill closes any gap that is itself plausible:

  Tier 1 – OSRM /route  (road-following geometry, exact road distance)
  Tier 2 – Google Dirs   (road-following geometry, exact road distance)
  Tier 3/4 – Haversine   (straight-line geometry, haversine distance — pure
             math, cannot fail)

Routed tiers are pinned to the direction of travel observed either side of the
gap, so the answer follows the carriageway actually driven rather than the
fastest legal path — without that hint a short gap spanning a divided road can
be "bridged" by a drive to the next turnaround and back.

A gap is left unbridged — recorded in ``failed_gaps`` instead of guessed at —
when it is not plausible: farther than ``MAX_INFERRED_CONNECTOR_KM``
regardless of reason, or (for an internal gap between two observed segments,
where real device timestamps bound both sides) longer than
``MAX_INFERRED_GAP_SECONDS``, or a routed answer that could not have been
driven in the time available (``MAX_CONNECTOR_IMPLIED_SPEED_KPH`` — measured
against two device fixes for an internal gap, and against the ride lifecycle
for an anchor connector once it is longer than ``ANCHOR_SPEED_GATE_MIN_KM``).
This is deliberate: a straight line across an implausible distance, a routed
guess across an outage too long to trust, or a detour nobody could have driven
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
    from .datetime_utils import parse_iso_utc
    from .metrics import inc as _metric_inc
    from .route_distance import compute_gap_route_via_google, compute_gap_route_via_osrm, snap_endpoint_via_osrm
    from .route_reconstruction_projection import bearing_deg, coordinate, distance_m, project_observed_sections
    from .route_segments import MAX_PLAUSIBLE_SPEED_KPH, SegmentedRoute
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.route_distance import (  # type: ignore
        compute_gap_route_via_google,
        compute_gap_route_via_osrm,
        snap_endpoint_via_osrm,
    )
    from utils.route_reconstruction_projection import (  # type: ignore
        bearing_deg,
        coordinate,
        distance_m,
        project_observed_sections,
    )
    from utils.route_segments import MAX_PLAUSIBLE_SPEED_KPH, SegmentedRoute  # type: ignore


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
# Slack above the crow-flies gap distance allowed of a routed connector here.
# route_distance's own default (2.0 km) is sized for the live trail, where an
# over-long connector is transient cosmetics; on a finalized route it becomes
# billed/audited distance, so a completed route asks for the strict value.
GAP_MAX_EXTRA_KM = 0.5
# Multiple of the crow-flies gap a routed answer may reach. route_distance's
# default is 5x, which past ~125 m is the loosest guard in the chain: on a
# 389 m gap it still admits nearly 2 km of unwitnessed road. A completed route
# asks for 3x, which with the absolute slack above leaves ordinary
# around-the-block routing intact while closing that headroom.
GAP_MAX_DETOUR_RATIO = 3.0
# A routed connector accepted at more than this multiple is legitimate but
# worth watching -- it is counted separately so the next bad case shows up on a
# dashboard rather than in a rider's complaint.
GAP_DETOUR_WATCH_RATIO = 2.0
# Physics gate on a routed connector: whatever road path is substituted for the
# missing GPS must be drivable in the time the gap actually took. A router
# answering a short gap with a drive to the next legal turnaround and back
# (opposite carriageways of a divided road) fails this by a wide margin. Shares
# route_segments' plausibility bar so both layers call the same speed impossible.
MAX_CONNECTOR_IMPLIED_SPEED_KPH = MAX_PLAUSIBLE_SPEED_KPH
# The speed gate above needs a clock on both sides of the gap. An internal gap
# has one: two real device fixes. An anchor connector (missing_start /
# missing_tail) does not — its only clock is the ride lifecycle, and its
# geometry may be anchor error rather than travel (a booked-dropoff
# substitution, an off-road pickup snapped from a parking lot). Below this
# length an anchor connector is therefore left to the distance guards, exactly
# as before; above it the fabrication is large enough to be worth holding to
# the same physics, which is what stops a tail connector running to
# MAX_INFERRED_CONNECTOR_KM unchallenged.
ANCHOR_SPEED_GATE_MIN_KM = 1.0


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


def _exit_bearing(section: Optional[dict]) -> Optional[float]:
    """Direction of travel at the END of an observed section — the heading with
    which the driver entered the gap that follows it."""
    coordinates = (section or {}).get("coordinates") or []
    if len(coordinates) < 2:
        return None
    return bearing_deg(coordinates[-2], coordinates[-1])


def _entry_bearing(section: Optional[dict]) -> Optional[float]:
    """Direction of travel at the START of an observed section — the heading with
    which the driver left the gap that precedes it."""
    coordinates = (section or {}).get("coordinates") or []
    if len(coordinates) < 2:
        return None
    return bearing_deg(coordinates[0], coordinates[1])


def _implied_speed_kph(distance_km: float, elapsed_seconds: Optional[float]) -> Optional[float]:
    """Speed a connector implies, or None when the gap carries no usable clock."""
    if elapsed_seconds is None or elapsed_seconds <= 0:
        return None
    return distance_km / (elapsed_seconds / 3600.0)


def _speed_refusal_kph(reason: str, distance_km: float, elapsed_seconds: Optional[float]) -> Optional[float]:
    """The implied speed a connector should be refused for, or None to keep it.

    Anchor connectors are held to the same ceiling but only once they are long
    enough for the guess to matter — see ``ANCHOR_SPEED_GATE_MIN_KM``.
    """
    implied = _implied_speed_kph(distance_km, elapsed_seconds)
    if implied is None or implied <= MAX_CONNECTOR_IMPLIED_SPEED_KPH:
        return None
    if reason != "internal_gap" and distance_km < ANCHOR_SPEED_GATE_MIN_KM:
        return None
    return implied


# Connector outcomes, per CLAUDE.md's spinr_<domain>_<metric>_<unit> naming.
# Every gap decision lands in exactly one of these, so a dashboard shows how
# much of the fleet's published distance is evidence and how much is guesswork
# -- and a rise in routed_high_detour is the early warning that a threshold
# here needs revisiting, before a rider has to notice.
_CONNECTOR_METRIC = "spinr_rides_route_connector_total"


def _count_connector(outcome: str) -> None:
    try:
        _metric_inc(_CONNECTOR_METRIC, {"outcome": outcome})
    except Exception:  # pragma: no cover — metrics must never break a finalize
        logger.debug("connector metric emit failed", exc_info=True)


def _elapsed_between(start: Any, end: Any) -> Optional[float]:
    """Seconds between two datetimes, or None when either is missing/inverted."""
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds >= 0 else None


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
    lifecycle: Optional[Dict[str, Any]] = None,
) -> dict:
    """Insert bounded route sections between ordered observed evidence.

    Uses a 4-tier gap fill strategy so reconstruction succeeds whenever GPS
    breadcrumbs exist. ``failed_gaps`` names each gap that was refused instead
    of bridged (see the module docstring for the three refusal reasons); an
    empty list means every gap was closed with evidence-backed geometry.
    """
    observed_sections = project_observed_sections(segmented, matched_route)
    # Ride lifecycle bounds, when the caller has them. They are the only clock
    # an anchor connector can be held to — the pickup/completion anchors carry
    # no device timestamp of their own. Optional so the offline evidence
    # analyser can keep calling without one.
    trip_started_at = parse_iso_utc((lifecycle or {}).get("ride_started_at") or (lifecycle or {}).get("started_at"))
    trip_completed_at = parse_iso_utc(
        (lifecycle or {}).get("ride_completed_at") or (lifecycle or {}).get("completed_at")
    )
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
        start: list[float],
        end: list[float],
        reason: str,
        elapsed_seconds: Optional[float] = None,
        *,
        start_bearing: Optional[float] = None,
        end_bearing: Optional[float] = None,
    ) -> None:
        """4-tier gap fill: OSRM → Google → Haversine — unless the gap itself
        is not believable, in which case it is left unbridged (see the
        distance/time caps below) rather than guessed at.

        ``start_bearing``/``end_bearing`` are the headings observed either side
        of the gap; they keep the routed answer on the carriageway actually
        driven. A routed answer that still could not have been driven in the
        gap's own elapsed time is refused outright rather than substituted.
        """
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
            _count_connector("refused_distance_cap")
            return

        # Time-outage refusal — deliberately internal_gap only. Both sides of
        # an internal gap are real device fixes, so a long interval there is a
        # genuine tracking outage. Anchors now carry an elapsed time too (the
        # ride lifecycle), but it measures something different: a driver can
        # sit parked for ten minutes before tapping complete without any
        # tracking having failed, so that interval must not read as an outage.
        # Anchors are held to the speed ceiling below instead.
        if reason == "internal_gap" and elapsed_seconds is not None and elapsed_seconds > MAX_INFERRED_GAP_SECONDS:
            logger.info(
                "gap fill refused for %s: %.0fs outage exceeds the %ds plausibility cap",
                reason,
                elapsed_seconds,
                MAX_INFERRED_GAP_SECONDS,
            )
            failed_gaps.append(f"{reason}_exceeds_time_cap")
            _count_connector("refused_time_cap")
            return

        connector_attempts += 1

        # If we've exceeded max connectors, use haversine (always succeeds).
        if connector_attempts > MAX_INFERRED_CONNECTORS:
            straight_connector_km += gap_distance / 1000.0
            output.append(_straight_line_segment(start, end, gap_distance, reason, connector_attempts))
            _count_connector("straight")
            return

        routed = None

        # Tier 1: OSRM road-following route, pinned to the observed headings so
        # the gap is mapped along the roads actually travelled.
        if osrm_url:
            routed = await compute_gap_route_via_osrm(
                start,
                end,
                osrm_url,
                start_bearing=start_bearing,
                end_bearing=end_bearing,
                max_extra_km=GAP_MAX_EXTRA_KM,
                max_detour_ratio=GAP_MAX_DETOUR_RATIO,
            )

        provider = "osrm_inferred"

        # Tier 2: Google Directions fallback
        if not routed and google_api_key:
            routed = await compute_gap_route_via_google(
                start,
                end,
                google_api_key,
                max_extra_km=GAP_MAX_EXTRA_KM,
                max_detour_ratio=GAP_MAX_DETOUR_RATIO,
            )
            provider = "google_inferred"

        if routed:
            distance_km, coordinates = routed
            # Physics refusal: a road path that could not have been driven in
            # the gap's own elapsed time is not what happened, so it is left
            # unbridged rather than swapped for a straight line that would
            # read as real evidence on the audit map.
            implied_kph = _speed_refusal_kph(reason, float(distance_km), elapsed_seconds)
            if implied_kph is not None:
                logger.info(
                    "gap fill refused for %s: routed %.3f km over %.0fs implies %.0f km/h (cap %d)",
                    reason,
                    float(distance_km),
                    elapsed_seconds,
                    implied_kph,
                    MAX_CONNECTOR_IMPLIED_SPEED_KPH,
                )
                failed_gaps.append(f"{reason}_implausible_detour")
                _count_connector("refused_speed")
                return
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
            ratio = (float(distance_km) * 1000.0 / gap_distance) if gap_distance > 0 else 0.0
            _count_connector("routed_high_detour" if ratio > GAP_DETOUR_WATCH_RATIO else "routed")
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
            _count_connector("straight")

    if observed_sections:
        if start_anchor is not None:
            # Lead-in: ride start -> first recorded fix.
            await append_connector(
                start_anchor,
                observed_sections[0]["coordinates"][0],
                "missing_start",
                _elapsed_between(trip_started_at, observed_sections[0].get("segment_start_captured_at")),
                end_bearing=_entry_bearing(observed_sections[0]),
            )
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
                    previous["coordinates"][-1],
                    section["coordinates"][0],
                    "internal_gap",
                    elapsed_seconds,
                    start_bearing=_exit_bearing(previous),
                    end_bearing=_entry_bearing(section),
                )
            output.append(_strip_internal_projection_fields(section))

        if end_anchor is not None:
            # Tail age: last recorded fix -> ride completion. Same quantity
            # route_segments._tail_quality already computes for reporting; this
            # is the first time it reaches the connector decision.
            await append_connector(
                observed_sections[-1]["coordinates"][-1],
                end_anchor,
                "missing_tail",
                _elapsed_between(observed_sections[-1].get("segment_end_captured_at"), trip_completed_at),
                start_bearing=_exit_bearing(observed_sections[-1]),
            )
        # else: no completion coordinate at all — skip tail connector.
    else:
        if start_anchor is not None and end_anchor is not None:
            await append_connector(
                start_anchor, end_anchor, "missing_start", _elapsed_between(trip_started_at, trip_completed_at)
            )

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
