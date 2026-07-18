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
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.route_distance import compute_segmented_road_route  # type: ignore
    from utils.route_segments import SegmentedRoute, segment_route  # type: ignore


logger = logging.getLogger(__name__)

ROUTE_FINALIZER_INTERVAL_SECONDS = 15
ROUTE_CLAIM_STALE_SECONDS = 5 * 60


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


def _matched_projection(matched_route: Dict[str, Any]) -> list[dict]:
    """Convert matcher output to route-contract segments without cross-gap joins."""
    projection: list[dict] = []
    for segment in matched_route.get("segments") or []:
        observed_index = segment.get("segment_index")
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


def _quality_projection(segmented: SegmentedRoute, matched_route: Dict[str, Any]) -> dict:
    quality = segmented.quality
    failures = matched_route.get("failures") or []
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
    }


def _final_status(segmented: SegmentedRoute, matched_route: Dict[str, Any]) -> str:
    if segmented.quality.missing_tail or matched_route.get("failures"):
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
        processing_status = _final_status(segmented, matched_route)
        quality = _quality_projection(segmented, matched_route)
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
                "road_matched_segments": _matched_projection(matched_route),
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
        await _publish_finalized_snapshot(
            ride_id,
            ride,
            revision,
            (matched_route.get("segments") and _matched_projection(matched_route)) or _observed_projection(segmented),
            quality,
            (route_row or {}).get("completion_point"),
        )
        return {"processing_status": processing_status, "route_revision": revision, "route_quality": quality}
    except Exception:
        logger.error("route finalization failed for ride_id=%s", ride_id, exc_info=True)
        return await _schedule_retry(ride_id, route_row, "provider_failure")
