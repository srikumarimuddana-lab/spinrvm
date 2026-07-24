"""GPS trip-distance computation shared by settlement and re-finalization.

Extracted verbatim from ``routes/drivers/ride_complete.py`` so the route
finalizer can recompute measured distances when a delayed offline tail batch
arrives after completion. The fare pipeline never calls this module directly —
fare authority stays in ``complete_ride``; this is the *measured* side only.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from .. import db_supabase
except ImportError:
    import db_supabase  # type: ignore

try:
    from ..geo_utils import calculate_distance
except ImportError:
    from geo_utils import calculate_distance  # type: ignore

try:
    from .datetime_utils import parse_iso_utc
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore

try:
    from .gps_filtering import collapse_stationary_clusters, filter_low_accuracy
except ImportError:
    pass  # type: ignore


logger = logging.getLogger(__name__)

# GPS sanity caps — reject segments that are physically impossible before
# summing. Without these, a single tower-handoff jump on a 7 km trip could
# inflate actual_distance_km to 90+ km (the ingestion-time filter in
# utils/location_integrity.py is not retroactively re-applied to stored
# breadcrumbs).
#   MAX_SEG_KM    — single ping-to-ping displacement. Even at 240 km/h with a
#                   30 s gap that's 2 km; 5 km is well above any realistic value.
#   MAX_SEG_KMH   — sustained ground speed. Saskatchewan max posted is
#                   110 km/h; allow 150 for downhill/overtake transients.
#   MAX_SEG_GAP_S — long gaps (background, signal loss) make straight-line
#                   distance unreliable; treat same as the duration cap.
MAX_SEG_KM = 5.0
MAX_SEG_KMH = 150.0
MAX_SEG_GAP_S = 300

# Downsample bound for the per-phase polylines stored on ride_routes.
MAX_PER_PHASE_POLYLINE_POINTS = 150

_PAGE = 1000
MAX_BREADCRUMBS = 10000  # ~11 h at 4s — beyond any real single trip

ROAD_SNAP_TIMEOUT_SECONDS = 1.5


@dataclass
class TripDistanceResult:
    """Everything ``complete_ride`` historically derived from the breadcrumb trail."""

    actual_distance_km: float
    pickup_to_driver_km: float = 0.0
    phase_distances: Dict[str, float] = field(default_factory=dict)
    phase_durations: Dict[str, int] = field(default_factory=dict)
    phase_polylines: Dict[str, list] = field(default_factory=dict)
    road_polyline: list = field(default_factory=list)
    gps_points_count: int = 0
    trip_points_count: int = 0
    rejected_segments: int = 0
    max_segment_gap_s: int = 0
    road_distance_provider: str = "haversine_filtered"
    actual_distance_km_haversine: Optional[float] = None
    actual_distance_km_road: Optional[float] = None
    route_quality: Dict[str, Any] = field(default_factory=lambda: {"confidence": "low", "reason": "no_gps_breadcrumbs"})


async def load_ride_breadcrumbs(ride_id: str) -> List[Dict[str, Any]]:
    """Page through ALL breadcrumbs for the ride (time-ordered).

    A single limit=1000 read would drop the tail of long, densely-sampled
    trips (>~67 min at the 4s background cadence), under-reporting
    billable/phase distance and the SGI trail. Bounded by a hard ceiling so a
    pathological trail can't blow up memory; per-phase and route polylines are
    downsampled downstream regardless of input size.
    """
    all_breadcrumbs: List[Dict[str, Any]] = []
    offset = 0
    while True:
        page = await db_supabase.get_rows(
            "driver_location_history",
            {"ride_id": ride_id},
            order="timestamp",
            limit=_PAGE,
            offset=offset,
        )
        if not page:
            break
        all_breadcrumbs.extend(page)
        if len(page) < _PAGE or len(all_breadcrumbs) >= MAX_BREADCRUMBS:
            break
        offset += _PAGE
    if len(all_breadcrumbs) >= MAX_BREADCRUMBS:
        logger.warning(
            f"GPS breadcrumbs hit ceiling {MAX_BREADCRUMBS} for ride {ride_id}; tail beyond this is not summed"
        )
    all_breadcrumbs = [b for b in all_breadcrumbs if b.get("lat") is not None and b.get("lng") is not None]
    all_breadcrumbs.sort(key=lambda b: str(b.get("timestamp", "")))
    return all_breadcrumbs


def _sum_phase_distances(
    breadcrumbs: List[Dict[str, Any]],
) -> "tuple[Dict[str, float], Dict[str, float], int, int]":
    """Spike-filtered per-phase haversine sum over a breadcrumb trail.

    Attributes each consecutive segment to the current point's tracking_phase,
    rejecting anomalous segments (distance/gap/speed caps) before adding them.
    Returns ``(phase_totals_km, phase_seconds, rejected_segments,
    max_segment_gap_s)``. Pure — no I/O, no mutation.
    """
    rejected_segments = 0
    max_segment_gap_s = 0
    phase_totals: Dict[str, float] = {}
    phase_secs: Dict[str, float] = {}
    for i in range(1, len(breadcrumbs)):
        prev = breadcrumbs[i - 1]
        curr = breadcrumbs[i]
        phase = curr.get("tracking_phase") or "unknown"
        seg_km = calculate_distance(prev["lat"], prev["lng"], curr["lat"], curr["lng"])

        t_prev = parse_iso_utc(prev.get("timestamp"))
        t_curr = parse_iso_utc(curr.get("timestamp"))
        delta = None
        if t_prev and t_curr:
            delta = (t_curr - t_prev).total_seconds()
            if delta is not None and delta > max_segment_gap_s:
                max_segment_gap_s = int(round(delta))

        # Reject anomalous segments before adding to phase totals.
        if seg_km > MAX_SEG_KM:
            rejected_segments += 1
            continue
        if delta is not None and delta > MAX_SEG_GAP_S:
            rejected_segments += 1
            continue
        if delta is not None and delta > 0:
            seg_kmh = seg_km / (delta / 3600.0)
            if seg_kmh > MAX_SEG_KMH:
                rejected_segments += 1
                continue

        phase_totals[phase] = phase_totals.get(phase, 0.0) + seg_km
        # Duration: only count if the gap is reasonable (< 5 min)
        # to avoid one stale breadcrumb inflating a phase by hours.
        if delta is not None and 0 < delta <= 300:
            phase_secs[phase] = phase_secs.get(phase, 0.0) + delta
    return phase_totals, phase_secs, rejected_segments, max_segment_gap_s


async def compute_trip_distances(
    all_breadcrumbs: List[Dict[str, Any]],
    *,
    ride_id: str,
    planned_distance: float,
    road_snap_timeout_s: float = ROAD_SNAP_TIMEOUT_SECONDS,
    filter_mode: str = "off",
) -> TripDistanceResult:
    """Compute per-phase and billable-portion distances from a breadcrumb trail.

    Spike-filtered haversine per phase, sparse-GPS fallback to the planned
    distance, and an optional road-snap recompute accepted only inside the
    1/3x-3x sanity band of the haversine baseline.

    ``filter_mode`` controls GPS noise filtering (accuracy gate + stationary
    collapse, see ``utils/gps_filtering.py``), applied BEFORE summation and the
    road-snap so jitter can't inflate the billed distance or be map-matched into
    invented street loops:
      * ``"off"``    legacy behaviour — no filtering.
      * ``"shadow"`` bill the raw trace, but also compute the filtered value and
                     disclose the delta (verifies the change on real traffic).
      * ``"on"``     bill the filtered trace; keep the raw value for reference.
    """
    result = TripDistanceResult(actual_distance_km=planned_distance)
    result.gps_points_count = len(all_breadcrumbs)
    if result.gps_points_count < 2:
        return result

    # Filter GPS noise on a compute-only copy (raw breadcrumbs are the SGI audit
    # trail and are never mutated). The filtered trace feeds billing + road-snap
    # only in "on" mode; "shadow" bills raw but measures the filtered delta.
    filter_stats: Dict[str, Any] = {}
    filtered_breadcrumbs = all_breadcrumbs
    if filter_mode in ("shadow", "on"):
        _acc_kept, _dropped, _null = filter_low_accuracy(all_breadcrumbs)
        filtered_breadcrumbs, _collapsed = collapse_stationary_clusters(_acc_kept)
        filter_stats = {
            "filter_mode": filter_mode,
            "dropped_low_accuracy": _dropped,
            "collapsed_stationary": _collapsed,
            "null_accuracy_points": _null,
        }

    compute_breadcrumbs = filtered_breadcrumbs if filter_mode == "on" else all_breadcrumbs

    # Compute per-phase distances (attribute each segment to the current point's
    # phase) and per-phase durations. Phase 1 (online_idle) is not expected
    # against a ride_id but tolerated if it shows up.
    phase_totals, phase_secs, rejected_segments, max_segment_gap_s = _sum_phase_distances(compute_breadcrumbs)
    if rejected_segments:
        logger.info(
            f"Ride {ride_id}: dropped {rejected_segments}/{max(0, len(compute_breadcrumbs) - 1)} "
            "GPS segments as anomalous (speed/distance/gap caps)"
        )
    result.rejected_segments = rejected_segments
    result.max_segment_gap_s = max_segment_gap_s
    result.phase_distances = {k: round(v, 3) for k, v in phase_totals.items()}
    result.phase_durations = {k: int(round(v)) for k, v in phase_secs.items()}

    # Actual distance = trip_in_progress only (the paid portion). Guard
    # against sparse GPS: if fewer than 5 trip_in_progress points were
    # recorded the haversine sum is essentially just a straight-line from
    # pickup to dropoff (equivalent to the booking-time haversine). In that
    # case keep planned_distance so the displayed km matches what the fare
    # was calculated on, rather than showing a misleadingly short GPS value.
    result.trip_points_count = sum(1 for b in compute_breadcrumbs if b.get("tracking_phase") == "trip_in_progress")
    actual_distance_km = round(result.phase_distances.get("trip_in_progress", 0.0), 2)
    if result.trip_points_count < 5:
        logger.warning(
            f"Ride {ride_id}: only {result.trip_points_count} trip_in_progress GPS points "
            f"— GPS data too sparse for accurate distance; keeping planned={planned_distance}km"
        )
        actual_distance_km = planned_distance
    elif actual_distance_km == 0:
        # >= 5 points recorded but every segment was rejected by the
        # speed/distance/gap caps (e.g. GPS dead zone, spoofed trace).
        logger.warning(
            f"Ride {ride_id}: {result.trip_points_count} trip_in_progress GPS points but all "
            f"segments rejected by anomaly filter — keeping planned={planned_distance}km"
        )
        actual_distance_km = planned_distance

    result.pickup_to_driver_km = round(result.phase_distances.get("navigating_to_pickup", 0.0), 2)

    # Shadow: also compute the filtered trip distance so the delta vs the
    # (billed) raw value is observable before flipping filtering on. On: record
    # the raw (unfiltered) value alongside the billed filtered value. Either way
    # the numbers land in route_quality for the rollout dashboards.
    if filter_mode == "shadow":
        f_totals, _f_secs, _f_rej, _f_gap = _sum_phase_distances(filtered_breadcrumbs)
        filtered_trip_km = round(f_totals.get("trip_in_progress", 0.0), 2)
        raw_trip_km = round(result.phase_distances.get("trip_in_progress", 0.0), 2)
        filter_stats["actual_distance_km_filtered"] = filtered_trip_km
        filter_stats["filtered_delta_km"] = round(raw_trip_km - filtered_trip_km, 3)
        logger.info(
            "[trip_distance] shadow filter ride %s: raw=%.2f filtered=%.2f delta=%.3f (dropped=%d collapsed=%d)",
            ride_id,
            raw_trip_km,
            filtered_trip_km,
            filter_stats["filtered_delta_km"],
            filter_stats["dropped_low_accuracy"],
            filter_stats["collapsed_stationary"],
        )
    elif filter_mode == "on":
        r_totals, _r_secs, _r_rej, _r_gap = _sum_phase_distances(all_breadcrumbs)
        filter_stats["actual_distance_km_unfiltered"] = round(r_totals.get("trip_in_progress", 0.0), 2)

    # Road-snapped recompute (P2): the haversine sum above is already
    # spike-protected by the speed/distance/gap caps, but it still
    # approximates straight-line distance between consecutive pings, missing
    # road curvature and turns. When the recompute succeeds AND its value is
    # within sanity range of the haversine baseline (1/3x to 3x), it wins.
    # Otherwise we log the discrepancy and stick with the haversine value —
    # a Maps outage or an empty response can't be allowed to corrupt billing.
    actual_distance_km_haversine = actual_distance_km
    actual_distance_km_road = None
    road_result = None
    try:
        try:
            from .route_distance import compute_road_route
        except ImportError:
            from utils.route_distance import compute_road_route  # type: ignore
        # Hard deadline: settlement targets <1s P95 and an unbounded provider
        # call here (OSRM/Google over up to 10k breadcrumbs) previously held
        # the driver's Complete tap for as long as the provider hung. On
        # timeout we settle on the spike-protected haversine value — the same
        # fallback as a provider error, and already an accepted billable
        # baseline (the 1/3x-3x sanity band treats haversine as the reference).
        road_result = await asyncio.wait_for(compute_road_route(compute_breadcrumbs), timeout=road_snap_timeout_s)
    except asyncio.TimeoutError:
        logger.warning(
            f"[trip_distance] road-snap recompute exceeded {road_snap_timeout_s}s for ride {ride_id}; keeping haversine"
        )
    except Exception:
        logger.warning(
            "[trip_distance] road-snap recompute raised; keeping haversine",
            exc_info=True,
        )
    if road_result is not None:
        actual_distance_km_road = road_result["distance_km"]
        lo = max(0.1, actual_distance_km_haversine / 3.0)
        hi = max(0.1, actual_distance_km_haversine * 3.0)
        if lo <= actual_distance_km_road <= hi:
            actual_distance_km = round(actual_distance_km_road, 2)
            result.road_distance_provider = str(road_result.get("provider") or "road_snapped")
            # Trusted match → persist the road-snapped geometry (saved to
            # ride_routes by the caller) for SGI / dispute map review.
            result.road_polyline = road_result.get("polyline") or []
        else:
            logger.warning(
                f"Ride {ride_id}: road-snap distance {actual_distance_km_road}km "
                f"out of sanity range [{lo:.2f}, {hi:.2f}] from haversine "
                f"{actual_distance_km_haversine}km — keeping haversine value"
            )

    result.actual_distance_km = actual_distance_km
    result.actual_distance_km_haversine = actual_distance_km_haversine
    result.actual_distance_km_road = actual_distance_km_road

    # Per-phase polylines for SGI / dispute tooling. Each phase is
    # downsampled so a long trip's payload stays bounded. Stored as
    # [lat, lng, iso_ts] tuples so the admin can replay the trip with timing
    # on the detail map.
    phases_to_split = ("navigating_to_pickup", "trip_in_progress")
    for phase in phases_to_split:
        pts = [b for b in compute_breadcrumbs if b.get("tracking_phase") == phase]
        if not pts:
            continue
        step = max(1, len(pts) // MAX_PER_PHASE_POLYLINE_POINTS)
        sampled = pts[::step]
        if sampled and sampled[-1] is not pts[-1]:
            sampled.append(pts[-1])
        result.phase_polylines[phase] = [
            [
                round(p["lat"], 6),
                round(p["lng"], 6),
                str(p.get("timestamp") or ""),
            ]
            for p in sampled
        ]

    rejected_ratio = rejected_segments / max(1, len(compute_breadcrumbs) - 1)
    if result.trip_points_count >= 20 and rejected_ratio <= 0.1 and max_segment_gap_s <= 120:
        confidence = "high"
    elif result.trip_points_count >= 5 and rejected_ratio <= 0.25 and max_segment_gap_s <= 300:
        confidence = "medium"
    else:
        confidence = "low"
    result.route_quality = {
        "confidence": confidence,
        "gps_points_count": result.gps_points_count,
        "trip_points_count": result.trip_points_count,
        "rejected_segments": rejected_segments,
        "rejected_segment_ratio": round(rejected_ratio, 3),
        "max_segment_gap_seconds": max_segment_gap_s,
        "distance_provider": result.road_distance_provider,
        "actual_distance_km_haversine": (
            round(float(actual_distance_km_haversine), 3) if actual_distance_km_haversine is not None else None
        ),
        "actual_distance_km_road_snapped": (
            round(float(actual_distance_km_road), 3) if actual_distance_km_road is not None else None
        ),
        "road_snap_accepted": bool(result.road_polyline),
    }
    # Disclose GPS-filtering effect (dropped/collapsed counts + shadow delta or
    # the raw value when filtering is on) alongside the confidence signals.
    if filter_stats:
        result.route_quality.update(filter_stats)
    return result
