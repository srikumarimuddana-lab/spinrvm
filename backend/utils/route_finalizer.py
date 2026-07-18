"""Deferred, revisioned actual-route finalization.

Completion settlement remains the authority for fare and lifecycle state. This
worker only transforms already-durable GPS evidence into display/audit geometry
and is safe to replay as a newer route revision arrives.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    from .. import db_supabase
except ImportError:
    import db_supabase  # type: ignore

try:
    from .route_distance import compute_segmented_road_route
    from .route_segments import SegmentedRoute, segment_route
except ImportError:
    from utils.route_distance import compute_segmented_road_route  # type: ignore
    from utils.route_segments import SegmentedRoute, segment_route  # type: ignore


logger = logging.getLogger(__name__)


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


async def _schedule_retry(ride_id: str, route_row: Optional[Dict[str, Any]], reason: str) -> Dict[str, Any]:
    retry_count = int((route_row or {}).get("retry_count") or 0) + 1
    retry_seconds = min(300, 15 * (2 ** min(retry_count - 1, 4)))
    next_retry_at = _now() + timedelta(seconds=retry_seconds)
    await db_supabase.update_one(
        "ride_routes",
        {"ride_id": ride_id},
        {
            "processing_status": "pending",
            "processing_claimed_at": None,
            "retry_count": retry_count,
            "next_retry_at": next_retry_at,
            "route_quality": {"finalization_reason": reason},
        },
        upsert=True,
    )
    return {"processing_status": "pending", "retry_count": retry_count, "next_retry_at": next_retry_at}


async def finalize_route(ride_id: str) -> Dict[str, Any]:
    """Produce a revisioned route projection from durable evidence only.

    It deliberately never updates ride fare, duration, or lifecycle columns.
    An upstream loop supplies replay-safe claims; direct calls are useful for
    tests and one-off recovery after a deferred upload.
    """
    route_row: Optional[Dict[str, Any]] = None
    try:
        route_row = await _get_route_row(ride_id)
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
        await db_supabase.update_one(
            "ride_routes",
            {"ride_id": ride_id},
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
            upsert=True,
        )
        return {"processing_status": processing_status, "route_revision": revision, "route_quality": quality}
    except Exception:
        logger.error("route finalization failed for ride_id=%s", ride_id, exc_info=True)
        return await _schedule_retry(ride_id, route_row, "provider_failure")
