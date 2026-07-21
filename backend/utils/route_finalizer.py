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
except ImportError:
    import db_supabase  # type: ignore

try:
    from .datetime_utils import parse_iso_utc
    from .route_distance import compute_segmented_road_route
    from .route_segments import SegmentedRoute, segment_route
    from .trip_distance import compute_trip_distances, load_ride_breadcrumbs
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.route_distance import compute_segmented_road_route  # type: ignore
    from utils.route_segments import SegmentedRoute, segment_route  # type: ignore


logger = logging.getLogger(__name__)

ROUTE_FINALIZER_INTERVAL_SECONDS = 15
ROUTE_CLAIM_STALE_SECONDS = 5 * 60

# Minimum change in measured distance before the stats columns on the rides
# row are rewritten. Keeps idempotent replays (same evidence, new revision)
# from churning the row and the audit table.
DISTANCE_RECOMPUTE_EPSILON_KM = 0.05


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _quality_projection(segmented: SegmentedRoute, matched_route: Dict[str, Any], drawable: bool) -> dict:
    quality = segmented.quality
    failures = matched_route.get("failures") or []
    real_failures = _has_real_matching_failures(matched_route)
    incomplete_reason = (
        "missing_completion_fix"
        if quality.missing_tail
        else "insufficient_route_points"
        if not drawable
        else "road_match_partial_failure"
        if real_failures
        else None
    )
    return {
        "coverage_ratio": quality.coverage_ratio,
        "point_count": quality.point_count,
        "segment_count": quality.segment_count,
        "rejected_point_count": quality.rejected_point_count,
        "max_gap_seconds": quality.max_gap_seconds,
        "missing_tail": quality.missing_tail,
        "completion_distance_m": quality.completion_distance_m,
        "completion_tolerance_m": quality.completion_tolerance_m,
        "distance_provider": matched_route.get("provider"),
        "matched_distance_km": matched_route.get("distance_km"),
        "matching_failure_count": len(failures),
        "finalization_reason": "complete" if not failures else "provider_partial_failure",
        "incomplete_reason": incomplete_reason,
    }


def _final_status(segmented: SegmentedRoute, matched_route: Dict[str, Any], drawable: bool) -> str:
    if not drawable or segmented.quality.missing_tail or _has_real_matching_failures(matched_route):
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
    retry_seconds = min(300, 15 * (2 ** min(retry_count - 1, 4)))
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
    while True:
        try:
            await route_finalizer_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("route finalizer tick failed", exc_info=True)
        await asyncio.sleep(interval_seconds)


async def _recompute_ride_distance_stats(ride_id: str, ride: Dict[str, Any], revision: int) -> None:
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
    new_actual = float(distances.actual_distance_km or 0)
    if abs(new_actual - previous_actual) <= DISTANCE_RECOMPUTE_EPSILON_KM:
        return

    update_fields: Dict[str, Any] = {
        "actual_distance_km": distances.actual_distance_km,
        "phase_distances": distances.phase_distances,
        "phase_durations": distances.phase_durations,
        "pickup_to_driver_km": distances.pickup_to_driver_km,
        "gps_points_count": distances.gps_points_count,
    }

    fare_lock = False
    try:
        try:
            from ..settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        fare_lock = ((await get_app_settings()) or {}).get("fare_lock_enabled", False)
    except Exception:
        logger.debug("fare_lock_enabled check failed during recompute, defaulting to False", exc_info=True)
    if fare_lock:
        # Mirrors complete_ride: under fare-lock distance_km is display-only
        # (the fare stays at the booking-time estimate), so keep it in step
        # with the measured value. Without fare-lock, distance_km fed the
        # settled fare and must not drift from it retroactively.
        update_fields["distance_km"] = distances.actual_distance_km

    # ride_metrics actuals (read cache for rider/admin detail UI).
    metrics = dict(ride.get("ride_metrics") or {})
    phases = dict(metrics.get("phases") or {})
    nav_phase = dict(phases.get("navigating_to_pickup") or {})
    nav_phase["actual_distance_km"] = round(float(distances.pickup_to_driver_km or 0), 3)
    trip_phase = dict(phases.get("trip_in_progress") or {})
    trip_phase["actual_distance_km"] = round(new_actual, 3)
    if distances.actual_distance_km_haversine is not None:
        trip_phase["actual_distance_km_haversine"] = round(float(distances.actual_distance_km_haversine), 3)
    if distances.actual_distance_km_road is not None:
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
            "new_phase_distances": distances.phase_distances,
            "trigger": "late_tail_refinalization",
        },
    )
    logger.info(
        "ride %s measured distance recomputed at route revision %s: %.2fkm -> %.2fkm",
        ride_id,
        revision,
        previous_actual,
        new_actual,
    )


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
        segmented = segment_route(points, ride, (route_row or {}).get("completion_point"))
        matched_route = await compute_segmented_road_route(list(segmented.observed_segments))
        display_segments = _matched_projection(segmented, matched_route)
        drawable = _has_drawable_route(display_segments)
        processing_status = _final_status(segmented, matched_route, drawable)
        quality = _quality_projection(segmented, matched_route, drawable)
        revision = int((route_row or {}).get("route_revision") or 0) + 1
        now = _now()
        updated = await db_supabase.update_one(
            "ride_routes",
            claim_filters,
            {
                "route_schema_version": 2,
                "route_revision": revision,
                "processing_status": processing_status,
                "observed_segments": _observed_projection(segmented),
                "road_matched_segments": display_segments,
                "route_quality": quality,
                "processing_claimed_at": None,
                "next_retry_at": None,
                "finalized_at": now,
                "computed_at": now,
            },
            upsert=False,
        )
        if updated is None:
            return {"processing_status": "superseded"}
        try:
            # Stats-only follow-up: late tail evidence changes the *measured*
            # distance shown on receipts/tiles, never the settled fare.
            await _recompute_ride_distance_stats(ride_id, ride, revision)
        except Exception:
            logger.error(
                "distance stats recompute failed for ride_id=%s (route revision %s kept)",
                ride_id,
                revision,
                exc_info=True,
            )
        if drawable:
            await _publish_finalized_snapshot(
                ride_id,
                ride,
                revision,
                display_segments,
                quality,
                (route_row or {}).get("completion_point"),
                finalized_at=now,
            )
        return {"processing_status": processing_status, "route_revision": revision, "route_quality": quality}
    except Exception:
        logger.error("route finalization failed for ride_id=%s", ride_id, exc_info=True)
        return await _schedule_retry(ride_id, route_row, "provider_failure")
