"""Deferred, revisioned actual-route finalization.

Completion settlement remains the authority for fare and lifecycle state. This
worker only transforms already-durable GPS evidence into display/audit geometry
and is safe to replay as a newer route revision arrives.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
except ImportError:
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore

try:
    from .datetime_utils import parse_iso_utc
    from .loop_monitor import record_heartbeat as _record_heartbeat
    from .metrics import inc as _metric_inc
    from .metrics import observe as _metric_observe
    from .route_distance import compute_segmented_road_route
    from .route_reconstruction import reconstruct_completed_route
    from .route_segments import SegmentedRoute, segment_route
    from .trip_distance import compute_trip_distances, load_ride_breadcrumbs
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import observe as _metric_observe  # type: ignore
    from utils.route_distance import compute_segmented_road_route  # type: ignore
    from utils.route_reconstruction import reconstruct_completed_route  # type: ignore
    from utils.route_segments import SegmentedRoute, segment_route  # type: ignore

# geo_utils lives at the backend root, one level above utils — keep its import in
# its own block so its parent-relative path can't drag the sibling imports above
# into the except branch when this module is loaded as ``utils.route_finalizer``.
try:
    from ..geo_utils import calculate_distance
except ImportError:
    from geo_utils import calculate_distance  # type: ignore


logger = logging.getLogger(__name__)

ROUTE_FINALIZER_INTERVAL_SECONDS = 15
ROUTE_CLAIM_STALE_SECONDS = 5 * 60
MAX_ROUTE_FINALIZER_RETRIES = 5

# Minimum change in measured distance before the stats columns on the rides
# row are rewritten. Keeps idempotent replays (same evidence, new revision)
# from churning the row and the audit table.
DISTANCE_RECOMPUTE_EPSILON_KM = 0.05

# Audit trigger label per resolved distance basis (ride_distance_recomputes.trigger
# is free text, so no migration is needed to add these).
_DISTANCE_RECOMPUTE_TRIGGER_BY_BASIS = {
    "observed": "route_reconstruction",
    "observed_legacy": "route_reconstruction",
    "reconstructed": "reconstructed_distance",
    "planned_estimated": "coverage_fallback",
    "gps_measured": "late_tail_refinalization",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _phase_3_points(points: list[Any], ride: Dict[str, Any]) -> list[Any]:
    """Select passenger-trip evidence while preserving invalid rows for rejection."""
    started_at = parse_iso_utc(ride.get("ride_started_at") or ride.get("started_at"))
    completed_at = parse_iso_utc(ride.get("ride_completed_at") or ride.get("completed_at"))
    if started_at is None or completed_at is None or completed_at < started_at:
        raise ValueError("ride_lifecycle_timestamp_missing")

    selected: list[Any] = []
    for point in points:
        captured_at = (
            parse_iso_utc(point.get("captured_at") or point.get("timestamp")) if isinstance(point, dict) else None
        )
        if captured_at is None or started_at <= captured_at <= completed_at:
            selected.append(point)
    return selected


async def _get_route_row(ride_id: str) -> Optional[Dict[str, Any]]:
    rows = await db_supabase.get_rows("ride_routes", {"ride_id": ride_id}, limit=1)
    return rows[0] if rows else None


async def mark_route_pending(ride_id: str, completion_point: Optional[Dict[str, Any]]) -> None:
    """Record immutable completion evidence and queue a new route projection."""
    await db_supabase.update_one(
        "ride_routes",
        {"ride_id": ride_id},
        {
            "route_schema_version": 2,
            "processing_status": "pending",
            "completion_point": completion_point,
            "processing_claimed_at": None,
            "next_retry_at": _now(),
            # A queued re-finalization may contain new evidence. Hide the old
            # image immediately; only the finalizer's current claim may attach
            # a revision-matched private snapshot again.
            "snapshot_revision": 0,
            "snapshot_object_path": None,
            "snapshot_url": None,
            "finalized_at": None,
        },
        upsert=True,
    )


def _observed_projection(segmented: SegmentedRoute) -> list[dict]:
    """Persist coordinates in their original segments, never as one flat line."""
    return [
        {
            "boundary_reason": segment.boundary_reason,
            "coordinates": [[round(float(point["lat"]), 6), round(float(point["lng"]), 6)] for point in segment.points],
        }
        for segment in segmented.observed_segments
    ]


def _matched_projection(segmented: SegmentedRoute, matched_route: Dict[str, Any]) -> list[dict]:
    """Project matched geometry, falling back per failed observed segment."""
    projection: list[dict] = []
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
    for observed_index, observed_segment in enumerate(segmented.observed_segments):
        segment = matched_by_segment.get(observed_index)
        if observed_index in failures_by_segment or not segment:
            projection.append(
                {
                    "source_segment_index": observed_index,
                    "provider": "observed_fallback",
                    "coordinates": [
                        [round(float(point["lat"]), 6), round(float(point["lng"]), 6)]
                        for point in observed_segment.points
                    ],
                }
            )
            continue
        for matched in segment.get("matched_segments") or []:
            coordinates = matched.get("polyline") or []
            if len(coordinates) < 2:
                continue
            projection.append(
                {
                    "source_segment_index": observed_index,
                    "chunk_index": matched.get("chunk_index"),
                    "provider": matched.get("provider"),
                    "coordinates": coordinates,
                }
            )
    return projection


def _has_real_matching_failures(matched_route: Dict[str, Any]) -> bool:
    failures = matched_route.get("failures") or []
    return any(
        failure.get("reason") not in ("provider_unavailable", "insufficient_points")
        for failure in failures
        if isinstance(failure, dict)
    )


def _has_drawable_route(route_segments: list[dict]) -> bool:
    """Return whether finalized evidence contains at least one drawable line."""
    return any(
        isinstance(segment, dict) and isinstance(segment.get("coordinates"), list) and len(segment["coordinates"]) >= 2
        for segment in route_segments
    )


def _quality_projection(
    segmented: SegmentedRoute,
    matched_route: Dict[str, Any],
    drawable: bool,
    reconstructed: Optional[Dict[str, Any]] = None,
) -> dict:
    quality = segmented.quality
    failures = matched_route.get("failures") or []
    real_failures = _has_real_matching_failures(matched_route)
    failed_gaps = (reconstructed or {}).get("failed_gaps") or []
    endpoints_verified = reconstructed is None or (
        reconstructed.get("endpoint_start_verified") is True and reconstructed.get("endpoint_end_verified") is True
    )
    incomplete_reason = (
        "missing_completion_fix"
        if quality.missing_tail and reconstructed is None
        else "osrm_reconstruction_failed"
        if failed_gaps or not endpoints_verified
        else "insufficient_route_points"
        if not drawable
        else "road_match_partial_failure"
        if real_failures
        else None
    )
    projected = {
        # coverage_ratio is now gap-aware (excludes mid-trip dead zones);
        # temporal_coverage_ratio keeps the first→last span figure.
        "coverage_ratio": quality.coverage_ratio,
        "temporal_coverage_ratio": quality.span_coverage_ratio,
        "point_count": quality.point_count,
        "segment_count": quality.segment_count,
        "rejected_point_count": quality.rejected_point_count,
        "max_gap_seconds": quality.max_gap_seconds,
        "missing_tail": quality.missing_tail,
        "completion_distance_m": quality.completion_distance_m,
        "completion_tolerance_m": quality.completion_tolerance_m,
        "distance_provider": matched_route.get("provider"),
        "matched_distance_km": (reconstructed or {}).get("observed_distance_km", matched_route.get("distance_km")),
        "matching_failure_count": len(failures) + len(failed_gaps),
        "finalization_reason": ("complete" if not failures and not failed_gaps else "provider_partial_failure"),
        "incomplete_reason": incomplete_reason,
    }
    if reconstructed is not None:
        projected.update(
            {
                "observed_distance_km": reconstructed.get("observed_distance_km"),
                "inferred_distance_km": reconstructed.get("inferred_distance_km"),
                "observed_distance_ratio": reconstructed.get("observed_distance_ratio"),
                "inferred_distance_ratio": reconstructed.get("inferred_distance_ratio"),
                "inferred_gap_count": reconstructed.get("inferred_gap_count"),
                "endpoint_start_verified": reconstructed.get("endpoint_start_verified"),
                "endpoint_end_verified": reconstructed.get("endpoint_end_verified"),
                "failed_gaps": list(failed_gaps),
            }
        )
    return projected


def _final_status(
    segmented: SegmentedRoute,
    matched_route: Dict[str, Any],
    drawable: bool,
    reconstructed: Optional[Dict[str, Any]] = None,
) -> str:
    reconstruction_incomplete = reconstructed is not None and (
        bool(reconstructed.get("failed_gaps"))
        or reconstructed.get("endpoint_start_verified") is not True
        or reconstructed.get("endpoint_end_verified") is not True
    )
    if (
        not drawable
        or (segmented.quality.missing_tail and reconstructed is None)
        or _has_real_matching_failures(matched_route)
        or reconstruction_incomplete
    ):
        return "incomplete"
    return "complete"


def _claim_filters(ride_id: str, route_row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build the compare-and-set filter for a worker-owned route claim."""
    if not route_row or route_row.get("processing_status") != "processing":
        return None
    claimed_at = route_row.get("processing_claimed_at")
    if claimed_at is None:
        return None
    return {
        "ride_id": ride_id,
        "processing_status": "processing",
        "processing_claimed_at": claimed_at,
    }


async def _schedule_retry(ride_id: str, route_row: Optional[Dict[str, Any]], reason: str) -> Dict[str, Any]:
    claim_filters = _claim_filters(ride_id, route_row)
    if claim_filters is None:
        return {"processing_status": "superseded"}
    retry_count = int((route_row or {}).get("retry_count") or 0) + 1
    retry_seconds = _retry_delay_seconds(retry_count)
    next_retry_at = _now() + timedelta(seconds=retry_seconds)
    updated = await db_supabase.update_one(
        "ride_routes",
        claim_filters,
        {
            "processing_status": "pending",
            "processing_claimed_at": None,
            "retry_count": retry_count,
            "next_retry_at": next_retry_at,
            "route_quality": {"finalization_reason": reason},
        },
        upsert=False,
    )
    if updated is None:
        return {"processing_status": "superseded"}
    return {"processing_status": "pending", "retry_count": retry_count, "next_retry_at": next_retry_at}


def _retry_delay_seconds(retry_count: int) -> int:
    return min(300, 15 * (2 ** min(max(retry_count - 1, 0), 4)))


async def _publish_finalized_snapshot(
    ride_id: str,
    ride: Dict[str, Any],
    route_revision: int,
    route_segments: list[dict],
    route_quality: dict,
    completion_point: Optional[dict],
    *,
    finalized_at: datetime,
) -> None:
    """Publish the route image for one immutable finalization revision.

    Snapshot publishing is intentionally downstream of finalization. Failure to
    render or upload an image never reopens a settled route or retries its map
    matcher; consumers instead show the route segment data or an unavailable
    snapshot state.
    """
    try:
        try:
            from ..routes.drivers._shared import _generate_and_store_ride_snapshot
        except ImportError:
            from routes.drivers._shared import _generate_and_store_ride_snapshot  # type: ignore

        await _generate_and_store_ride_snapshot(
            ride_id=ride_id,
            pickup_lat=ride.get("pickup_lat"),
            pickup_lng=ride.get("pickup_lng"),
            dropoff_lat=ride.get("dropoff_lat"),
            dropoff_lng=ride.get("dropoff_lng"),
            phase_polylines=None,
            route_polyline=None,
            route_segments=route_segments,
            completion_point=completion_point,
            route_quality=route_quality,
            route_revision=route_revision,
            finalized_at=finalized_at,
        )
    except Exception:
        logger.error("route snapshot publishing failed for ride_id=%s", ride_id, exc_info=True)


def _is_due(route: Dict[str, Any], now: datetime) -> bool:
    retry_at = parse_iso_utc(route.get("next_retry_at"))
    return retry_at is None or retry_at <= now


async def claim_next_pending_route(candidates: Optional[list[Dict[str, Any]]] = None) -> Optional[str]:
    """Atomically claim one due pending route; losers receive ``None``."""
    if candidates is None:
        candidates = await db_supabase.get_rows(
            "ride_routes",
            {"processing_status": "pending"},
            order="next_retry_at",
            limit=20,
        )
    now = _now()
    for route in candidates:
        ride_id = route.get("ride_id")
        if not ride_id or not _is_due(route, now):
            continue
        updated = await db_supabase.update_one(
            "ride_routes",
            {"ride_id": ride_id, "processing_status": "pending"},
            {"processing_status": "processing", "processing_claimed_at": now},
        )
        if updated is not None:
            return str(ride_id)
    return None


async def recover_stale_route_claims() -> int:
    """Return processing claims older than five minutes to the durable queue."""
    routes = await db_supabase.get_rows(
        "ride_routes",
        {"processing_status": "processing"},
        order="processing_claimed_at",
        limit=100,
    )
    recovered = 0
    now = _now()
    for route in routes:
        claimed_at = parse_iso_utc(route.get("processing_claimed_at"))
        ride_id = route.get("ride_id")
        if not ride_id or claimed_at is None or (now - claimed_at).total_seconds() <= ROUTE_CLAIM_STALE_SECONDS:
            continue
        updated = await db_supabase.update_one(
            "ride_routes",
            {
                "ride_id": ride_id,
                "processing_status": "processing",
                "processing_claimed_at": route.get("processing_claimed_at"),
            },
            {"processing_status": "pending", "processing_claimed_at": None, "next_retry_at": now},
        )
        if updated is not None:
            recovered += 1
    return recovered


BACKSTOP_SWEEP_EVERY_TICKS = 20  # ~5 min at the 15 s tick
BACKSTOP_GRACE_SECONDS = 5 * 60
BACKSTOP_LOOKBACK_DAYS = 7
BACKSTOP_BATCH_LIMIT = 25


async def sweep_unsettled_completed_rides() -> int:
    """Settle completed rides that have no ride_routes row (missed settlement).

    Every completion writer is supposed to create the ride_routes row and queue
    finalization; the rider-end and admin force-complete paths historically
    didn't (ride SPR-PE7TTB: 51 breadcrumbs stored, gps_points_count=0, no
    route, no period-distance audit). This sweep heals recent history and any
    future missed writer. Replay-safe: settle_completed_ride_geometry skips
    rides that already have a ride_routes row, and its underlying writers are
    idempotent, so concurrent replicas collapse harmlessly.
    """
    # Lazy import: ride_settlement imports mark_route_pending from this module.
    try:
        from .ride_settlement import settle_completed_ride_geometry
    except ImportError:
        from utils.ride_settlement import settle_completed_ride_geometry  # type: ignore

    now = _now()
    rides = await db_supabase.get_rows(
        "rides",
        {
            "status": "completed",
            "ride_completed_at": {
                "$gte": (now - timedelta(days=BACKSTOP_LOOKBACK_DAYS)).isoformat(),
                # Grace window so a normal driver completion (which writes the
                # row synchronously) is never raced by the sweep.
                "$lte": (now - timedelta(seconds=BACKSTOP_GRACE_SECONDS)).isoformat(),
            },
            "driver_id": {"$notnull": True},
        },
        order="ride_completed_at",
        desc=True,
        limit=200,
        columns="id",
    )
    if not rides:
        return 0
    ride_ids = [r["id"] for r in rides if r.get("id")]
    settled_rows = await db_supabase.get_rows(
        "ride_routes", {"ride_id": {"$in": ride_ids}}, limit=len(ride_ids), columns="ride_id"
    )
    already = {r.get("ride_id") for r in settled_rows}
    missing = [rid for rid in ride_ids if rid not in already]
    settled = 0
    for rid in missing[:BACKSTOP_BATCH_LIMIT]:
        if await settle_completed_ride_geometry(rid, trigger="backstop_sweep"):
            settled += 1
    if missing:
        logger.info(
            "settlement backstop: %d/%d unsettled completed rides handled this sweep",
            settled,
            len(missing),
        )
    return settled


async def route_finalizer_tick() -> int:
    """Recover stale work, atomically claim one route, then finalize it."""
    await recover_stale_route_claims()
    ride_id = await claim_next_pending_route()
    if not ride_id:
        return 0
    await finalize_route(ride_id)
    return 1


async def route_finalizer_loop(interval_seconds: int = ROUTE_FINALIZER_INTERVAL_SECONDS) -> None:
    """Replay-safe 15-second loop for versioned route finalization."""
    tick = 0
    while True:
        try:
            # Backstop first on the sweep ticks so a healed ride can be
            # claimed by this same tick's finalization pass.
            if tick % BACKSTOP_SWEEP_EVERY_TICKS == 0:
                try:
                    await sweep_unsettled_completed_rides()
                except Exception:
                    logger.error("settlement backstop sweep failed", exc_info=True)
            await route_finalizer_tick()
            _record_heartbeat("route_finalizer (15s)")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("route finalizer tick failed", exc_info=True)
        tick += 1
        await asyncio.sleep(interval_seconds)


def _endpoint_straight_line_km(ride: Dict[str, Any]) -> float:
    """Crow-flies pickup→dropoff distance, the physical floor for any trip."""
    try:
        return calculate_distance(
            float(ride["pickup_lat"]),
            float(ride["pickup_lng"]),
            float(ride["dropoff_lat"]),
            float(ride["dropoff_lng"]),
        )
    except (KeyError, TypeError, ValueError):
        return 0.0


def resolve_measured_distance_km(
    reconstructed: Optional[Dict[str, Any]],
    *,
    coverage: float,
    planned_km: float,
    straight_line_km: float,
    min_coverage: float = 0.6,
    max_straight_share: float = 0.25,
    min_vs_straight: float = 0.8,
) -> "tuple[float, str]":
    """Decide the measured distance a completed ride should DISPLAY (never bill).

    The old code wrote ``observed_distance_km`` (map-matched GPS only), which for
    a trip with a mid-route dropout is just the jitter near the ends — the 2.6 km
    that under-reported the 1.8 km incident. This weighs the evidence instead and
    returns ``(distance_km, basis)``:

      * ``observed`` — good coverage and the gaps (if any) were closed by real
        road connectors → observed + routed-connector distance.
      * ``reconstructed`` — coverage is poor but road connectors still bridged
        the gaps → same value, flagged as partly inferred.
      * ``planned_estimated`` — the gaps couldn't be routed (straight-line
        connectors dominate) OR the candidate is physically impossible (below
        ``min_vs_straight`` × the crow-flies endpoints distance) → keep the
        planned/booked distance rather than publish a wrong GPS number.

    Straight-line connector distance is NEVER included in the number — a blind
    chord across an unrouted gap is not a believable road distance.
    """
    if not reconstructed:
        return round(float(planned_km or 0), 3), "planned_estimated"

    observed = float(reconstructed.get("observed_distance_km") or 0)
    routed = float(reconstructed.get("routed_connector_distance_km") or 0)
    straight = float(reconstructed.get("straight_connector_distance_km") or 0)
    candidate = round(observed + routed, 3)
    denom = candidate + straight
    straight_share = (straight / denom) if denom > 0 else 0.0

    # Physically-impossible floor: a measured distance far below the straight
    # line between endpoints cannot be a real trip (2.6 km vs ~5.5 km crow-flies
    # in the incident) — fall back to the booked estimate.
    if straight_line_km and candidate < min_vs_straight * float(straight_line_km):
        return round(float(planned_km or 0), 3), "planned_estimated"

    if candidate > 0 and straight_share <= max_straight_share:
        if coverage >= min_coverage:
            return candidate, "observed"
        return candidate, "reconstructed"

    # Low coverage AND the gaps are straight-line (unrouted) — not trustworthy.
    return round(float(planned_km or 0), 3), "planned_estimated"


async def _recompute_ride_distance_stats(
    ride_id: str,
    ride: Dict[str, Any],
    revision: int,
    reconstructed: Optional[Dict[str, Any]] = None,
    coverage: float = 0.0,
) -> None:
    """Refresh measured-distance stats on a completed ride from full evidence.

    Runs after a successful revisioned projection write, when a late tail
    batch may have extended the breadcrumb trail past what ``complete_ride``
    saw at settlement time. Updates stats/display columns ONLY:
    ``actual_distance_km``, ``phase_distances``, ``phase_durations``,
    ``pickup_to_driver_km``, ``gps_points_count`` and the ``ride_metrics``
    actuals (plus ``distance_km`` under fare-lock, where it is display-only
    because the rider paid the booking-time fare). Fare columns and
    ``fare_breakdown_snapshot`` are NEVER touched — recomputing a settled
    fare is out of scope by design. Every applied change writes an
    append-only ``ride_distance_recomputes`` audit row.
    """
    if ride.get("status") != "completed":
        return

    breadcrumbs = await load_ride_breadcrumbs(ride_id)
    planned_distance = ride.get("planned_distance_km") or ride.get("distance_km", 0) or 0
    distances = await compute_trip_distances(
        breadcrumbs,
        ride_id=ride_id,
        planned_distance=planned_distance,
    )

    previous_actual = float(ride.get("actual_distance_km") or 0)

    # One settings fetch: fare-lock plus the distance-resolution knobs.
    fare_lock = False
    min_coverage = 0.6
    fallback_enabled = True
    try:
        _settings = (await get_app_settings()) or {}
        fare_lock = _settings.get("fare_lock_enabled", False)
        min_coverage = float(_settings.get("route_min_observed_coverage_ratio", 0.6))
        fallback_enabled = bool(_settings.get("route_distance_fallback_enabled", True))
    except Exception:
        logger.debug("distance-resolution settings read failed during recompute; using defaults", exc_info=True)

    # Decide the measured distance to DISPLAY. The reconstruction path weighs
    # observed + routed-connector distance against coverage and the crow-flies
    # floor; a broken trace falls back to the planned/booked distance instead of
    # publishing a wrong GPS number (the 2.6 km-for-1.8 km symptom). Fare is
    # never touched here. Late-tail-only recompute (no reconstruction) keeps the
    # existing GPS-measured value.
    if reconstructed is not None and fallback_enabled:
        new_actual, distance_basis = resolve_measured_distance_km(
            reconstructed,
            coverage=coverage,
            planned_km=planned_distance,
            straight_line_km=_endpoint_straight_line_km(ride),
            min_coverage=min_coverage,
        )
        new_actual = float(new_actual)
    elif reconstructed is not None:
        # Fallback disabled — legacy observed-only behaviour, flagged as such.
        new_actual = float(reconstructed.get("observed_distance_km") or 0)
        distance_basis = "observed_legacy"
    else:
        new_actual = float(distances.actual_distance_km or 0)
        distance_basis = "gps_measured"

    if abs(new_actual - previous_actual) <= DISTANCE_RECOMPUTE_EPSILON_KM:
        return

    phase_distances = dict(distances.phase_distances or {})
    phase_distances["trip_in_progress"] = round(new_actual, 3)
    update_fields: Dict[str, Any] = {
        "actual_distance_km": round(new_actual, 3),
        "phase_distances": phase_distances,
        "phase_durations": distances.phase_durations,
        "pickup_to_driver_km": distances.pickup_to_driver_km,
        "gps_points_count": distances.gps_points_count,
    }

    if fare_lock:
        # Mirrors complete_ride: under fare-lock distance_km is display-only
        # (the fare stays at the booking-time estimate), so keep it in step
        # with the measured value. Without fare-lock, distance_km fed the
        # settled fare and must not drift from it retroactively.
        update_fields["distance_km"] = round(new_actual, 3)

    # ride_metrics actuals (read cache for rider/admin detail UI).
    metrics = dict(ride.get("ride_metrics") or {})
    phases = dict(metrics.get("phases") or {})
    nav_phase = dict(phases.get("navigating_to_pickup") or {})
    nav_phase["actual_distance_km"] = round(float(distances.pickup_to_driver_km or 0), 3)
    trip_phase = dict(phases.get("trip_in_progress") or {})
    trip_phase["actual_distance_km"] = round(new_actual, 3)
    trip_phase["distance_basis"] = distance_basis
    if distances.actual_distance_km_haversine is not None:
        trip_phase["actual_distance_km_haversine"] = round(float(distances.actual_distance_km_haversine), 3)
    if reconstructed is not None:
        trip_phase["actual_distance_km_road_snapped"] = round(new_actual, 3)
    elif distances.actual_distance_km_road is not None:
        trip_phase["actual_distance_km_road_snapped"] = round(float(distances.actual_distance_km_road), 3)
    phases["navigating_to_pickup"] = nav_phase
    phases["trip_in_progress"] = trip_phase
    metrics["phases"] = phases
    update_fields["ride_metrics"] = metrics

    # Status filter keeps this replay-safe against any concurrent lifecycle
    # writer; a completed ride can never leave completed.
    applied = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "status": "completed"},
        update_fields,
        upsert=False,
    )
    if applied is None:
        return

    await db_supabase.insert_one(
        "ride_distance_recomputes",
        {
            "ride_id": ride_id,
            "route_revision": revision,
            "previous_actual_distance_km": previous_actual,
            "new_actual_distance_km": new_actual,
            "previous_phase_distances": ride.get("phase_distances") or {},
            "new_phase_distances": phase_distances,
            "trigger": _DISTANCE_RECOMPUTE_TRIGGER_BY_BASIS.get(distance_basis, "route_reconstruction"),
        },
    )
    logger.info(
        "ride %s measured distance recomputed at route revision %s: %.2fkm -> %.2fkm (basis=%s)",
        ride_id,
        revision,
        previous_actual,
        new_actual,
        distance_basis,
    )

    # Insurer audit correction: the SGI per-period distances were frozen at
    # settlement; late evidence that shifted the measured distance beyond the
    # epsilon appends revision rows (append-only-safe, migration 334). Readers
    # use the driver_period_distances_current view. Best-effort.
    driver_id = ride.get("driver_id")
    if driver_id:
        try:
            try:
                from .metrics import inc as _rev_metric_inc
                from .period_distance_audit import record_period_distance_revision
            except ImportError:
                from utils.metrics import inc as _rev_metric_inc  # type: ignore
                from utils.period_distance_audit import record_period_distance_revision  # type: ignore

            revised_p3 = await record_period_distance_revision(
                driver_id=driver_id, ride_id=ride_id, period=3, distance_km=round(new_actual, 3)
            )
            revised_p2 = await record_period_distance_revision(
                driver_id=driver_id,
                ride_id=ride_id,
                period=2,
                distance_km=round(float(distances.pickup_to_driver_km or 0), 3),
            )
            if revised_p3 or revised_p2:
                _rev_metric_inc("spinr_insurance_period_distance_rederived_total")
        except Exception:
            logger.error("period-distance revision append failed for ride %s", ride_id, exc_info=True)


async def finalize_route(ride_id: str) -> Dict[str, Any]:
    """Produce a revisioned route projection from durable evidence only.

    It deliberately never updates ride fare, duration, or lifecycle columns.
    Only the durable worker may invoke it after atomically claiming the route.
    Every write keeps the claim token in its filter so later GPS evidence can
    requeue the route without being overwritten by an in-flight projection.
    """
    route_row: Optional[Dict[str, Any]] = None
    try:
        route_row = await _get_route_row(ride_id)
        claim_filters = _claim_filters(ride_id, route_row)
        if claim_filters is None:
            return {"processing_status": "superseded"}
        ride = await db_supabase.get_ride(ride_id)
        if not ride:
            raise ValueError("ride_not_found")
        points = await db_supabase.get_rows(
            "driver_location_history",
            {"ride_id": ride_id},
            order="captured_at",
            limit=10_000,
        )
        completion_point = (route_row or {}).get("completion_point")
        segmented = segment_route(_phase_3_points(points, ride), ride, completion_point)
        matched_route = await compute_segmented_road_route(list(segmented.observed_segments))
        reconstructed: Optional[Dict[str, Any]] = None
        if (
            ride.get("pickup_lat") is not None
            and ride.get("pickup_lng") is not None
            and isinstance(completion_point, dict)
            and completion_point.get("lat") is not None
            and completion_point.get("lng") is not None
        ):
            reconstructed = await reconstruct_completed_route(
                segmented,
                matched_route,
                {"lat": ride.get("pickup_lat"), "lng": ride.get("pickup_lng")},
                completion_point,
            )
        display_segments = (
            reconstructed["segments"] if reconstructed is not None else _matched_projection(segmented, matched_route)
        )
        drawable = _has_drawable_route(display_segments)
        processing_status = _final_status(segmented, matched_route, drawable, reconstructed)
        quality = _quality_projection(segmented, matched_route, drawable, reconstructed)
        # Corroborate the gap-aware coverage with the active-trip gap monitor's
        # record: how many dead zones opened during the ride and their total
        # duration. Timestamp-only rows (no coordinates); best-effort so a read
        # failure never fails finalization.
        try:
            gap_rows = await db_supabase.get_rows(
                "ride_location_gap_events",
                {"ride_id": ride_id},
                limit=200,
                columns="gap_seconds",
            )
            if gap_rows:
                quality = {
                    **quality,
                    "gap_event_count": len(gap_rows),
                    "gap_event_seconds": sum(int(g.get("gap_seconds") or 0) for g in gap_rows),
                }
        except Exception:
            logger.debug("gap-event read for route_quality failed for ride_id=%s", ride_id, exc_info=True)
        revision = int((route_row or {}).get("route_revision") or 0) + 1
        now = _now()
        # NOTE: With the 4-tier gap fill in route_reconstruction.py, failed_gaps
        # is always empty and endpoints are always verified.  The condition below
        # evaluates to False for all new rides.  Kept as a safety net for any
        # edge case where reconstruction is bypassed entirely.
        retryable_reconstruction = (
            processing_status == "incomplete" and quality.get("incomplete_reason") == "osrm_reconstruction_failed"
        )
        retry_count = int((route_row or {}).get("retry_count") or 0)
        next_retry_at = None
        finalized_at: Optional[datetime] = now
        if retryable_reconstruction:
            retry_count += 1
            if retry_count < MAX_ROUTE_FINALIZER_RETRIES:
                processing_status = "pending"
                quality["reconstruction_status"] = "retrying"
                next_retry_at = now + timedelta(seconds=_retry_delay_seconds(retry_count))
                finalized_at = None
            else:
                quality["reconstruction_status"] = "failed"

        route_payload: Dict[str, Any] = {
            "route_schema_version": 2,
            "route_revision": revision,
            "processing_status": processing_status,
            "observed_segments": _observed_projection(segmented),
            "road_matched_segments": display_segments,
            "route_quality": quality,
            "processing_claimed_at": None,
            "next_retry_at": next_retry_at,
            "finalized_at": finalized_at,
            "computed_at": now,
        }
        if retryable_reconstruction:
            route_payload["retry_count"] = retry_count
        if processing_status != "complete":
            route_payload.update(
                {
                    "snapshot_revision": 0,
                    "snapshot_object_path": None,
                    "snapshot_url": None,
                }
            )
        updated = await db_supabase.update_one(
            "ride_routes",
            claim_filters,
            route_payload,
            upsert=False,
        )
        if updated is None:
            return {"processing_status": "superseded"}
        # Fleet-health signal: outcome mix (complete / incomplete / pending
        # retry) and the coverage distribution. A rising incomplete share or a
        # sagging coverage histogram is the earliest sign of capture loss —
        # both were invisible when SPR-PE7TTB-class failures happened.
        _metric_inc("spinr_rides_route_finalized_total", {"status": str(processing_status)})
        _cov = quality.get("coverage_ratio")
        if isinstance(_cov, (int, float)):
            # Ratio-scaled buckets (observe() defaults to millisecond buckets).
            _metric_observe(
                "spinr_rides_route_coverage_ratio",
                float(_cov),
                buckets=(0.1, 0.25, 0.5, 0.7, 0.85, 0.95, 1.0),
            )
        if processing_status == "complete":
            try:
                # Stats-only follow-up: late tail evidence changes the *measured*
                # distance shown on receipts/tiles, never the settled fare.
                await _recompute_ride_distance_stats(
                    ride_id,
                    ride,
                    revision,
                    reconstructed=reconstructed,
                    coverage=segmented.quality.coverage_ratio,
                )
            except Exception:
                logger.error(
                    "distance stats recompute failed for ride_id=%s (route revision %s kept)",
                    ride_id,
                    revision,
                    exc_info=True,
                )
        if processing_status == "complete" and drawable:
            await _publish_finalized_snapshot(
                ride_id,
                ride,
                revision,
                display_segments,
                quality,
                completion_point,
                finalized_at=now,
            )
        return {"processing_status": processing_status, "route_revision": revision, "route_quality": quality}
    except Exception:
        logger.error("route finalization failed for ride_id=%s", ride_id, exc_info=True)
        return await _schedule_retry(ride_id, route_row, "provider_failure")
