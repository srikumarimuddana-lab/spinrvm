"""Standalone GPS-geometry settlement for rides completed outside the driver path.

Driver completion (routes/drivers/ride_complete.py) settles GPS geometry inline
as part of its atomic status flip and remains the authority for that path. This
module settles the SAME artifacts for rides that reach ``completed`` through the
other writers — the rider "end ride early" flow (routes/rides/lifecycle.py),
admin manual completion (routes/admin/rides.py), and the backstop sweep for
already-completed rides — which previously skipped GPS settlement entirely.
Found via ride SPR-PE7TTB: 51 stored breadcrumbs, but rides.gps_points_count=0,
no ride_routes row, no period-distance audit rows, and the route finalizer was
never queued, because the rider ended the ride.

Replay-safe: a ride with an existing ride_routes row is treated as already
settled and skipped, and every underlying writer is idempotent
(record_ride_period_distances checks existing rows; mark_route_pending and the
route_payload write are upserts). ``settle_completed_ride_geometry`` never
raises — geometry settlement is best-effort and must not disturb the completion
that already happened.

Scope: geometry/audit only. This module must NEVER touch fare fields,
``distance_km`` (the billed value), ``status``, or payment state — those belong
to the completion writer that already ran. The rider-end flow charges the full
booked fare by policy; writing the measured distance into ``actual_distance_km``
is audit/display only.

If the aggregation steps in ride_complete.py change (new fields, new filter
behavior), mirror them here — the shared math lives in utils.trip_distance /
utils.period_distance_audit / utils.route_finalizer, so drift risk is limited
to this orchestration.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from .. import db_supabase
except ImportError:
    import db_supabase  # type: ignore

try:
    from .breadcrumb_buffer import flush_driver_breadcrumbs
    from .metrics import inc as _metric_inc
    from .period_distance_audit import record_ride_period_distances
    from .route_finalizer import mark_route_pending
    from .trip_distance import compute_trip_distances, load_ride_breadcrumbs
except ImportError:
    from utils.breadcrumb_buffer import flush_driver_breadcrumbs  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.period_distance_audit import record_ride_period_distances  # type: ignore
    from utils.route_finalizer import mark_route_pending  # type: ignore
    from utils.trip_distance import compute_trip_distances, load_ride_breadcrumbs  # type: ignore

logger = logging.getLogger(__name__)


async def _get_gps_distance_filter_mode() -> str:
    """Same contract as ride_complete._get_gps_distance_filter_mode.

    Kept as a local copy (not imported) so utils never imports from routes;
    a settings read failure degrades to "off" rather than blocking settlement.
    """
    try:
        try:
            from ..settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        settings = await get_app_settings()
    except Exception:
        logger.error("standalone settlement gps-filter configuration read failed; defaulting to off", exc_info=True)
        return "off"
    mode = str((settings or {}).get("gps_distance_filter_mode", "shadow")).lower()
    if mode not in {"off", "shadow", "on"}:
        logger.warning("gps_distance_filter_mode invalid (%s); defaulting to off", mode)
        return "off"
    return mode


async def settle_completed_ride_geometry(ride_id: str, *, trigger: str) -> bool:
    """Settle GPS geometry for an already-completed ride. Returns True if settled.

    ``trigger`` names the caller for logs/metrics/completion evidence:
    "rider_end" | "admin_complete" | "backstop_sweep".
    """
    try:
        return await _settle(ride_id, trigger=trigger)
    except Exception:
        # Never let geometry settlement raise into a completion flow or loop.
        logger.error(
            "standalone geometry settlement failed for ride %s (trigger=%s)",
            ride_id,
            trigger,
            exc_info=True,
        )
        _metric_inc("spinr_rides_geometry_settled_total", {"trigger": trigger, "outcome": "error"})
        return False


async def _settle(ride_id: str, *, trigger: str) -> bool:
    ride = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("rides", {"id": ride_id}, limit=1))
    if not ride or ride.get("status") != "completed":
        logger.info("settlement skipped for ride %s: not found or not completed (trigger=%s)", ride_id, trigger)
        return False
    driver_id = ride.get("driver_id")
    if not driver_id:
        # No assigned driver → no breadcrumbs and no insurance periods to audit.
        return False

    # Replay guard: any existing ride_routes row means a settlement path
    # (driver inline, a previous standalone run, or a late-tail requeue)
    # already owns this ride's geometry.
    existing_route = await db_supabase.get_rows("ride_routes", {"ride_id": ride_id}, limit=1)
    if existing_route:
        return False

    # Drain any WS-buffered points before reading the trail (same as the
    # driver path's B3.3 flush; best-effort, only helps when the driver's WS
    # session is on this replica).
    try:
        await flush_driver_breadcrumbs(driver_id)
    except Exception:
        logger.error("[settlement] breadcrumb flush failed for driver %s", driver_id, exc_info=True)

    planned_distance = ride.get("planned_distance_km") or ride.get("distance_km", 0) or 0
    actual_distance_km = planned_distance
    phase_distances: Dict[str, float] = {}
    phase_durations: Dict[str, int] = {}
    phase_polylines: Dict[str, list] = {}
    pickup_to_driver_km = 0.0
    road_polyline: list = []
    gps_points_count = 0
    route_quality: Dict[str, Any] = {"confidence": "low", "reason": "no_gps_breadcrumbs"}
    route_geometry_status = "pending"
    route_geometry_error: Optional[str] = None

    try:
        gps_filter_mode = await _get_gps_distance_filter_mode()
        all_breadcrumbs = await load_ride_breadcrumbs(ride_id)
        distances = await compute_trip_distances(
            all_breadcrumbs,
            ride_id=ride_id,
            planned_distance=planned_distance,
            filter_mode=gps_filter_mode,
        )
        actual_distance_km = distances.actual_distance_km
        phase_distances = distances.phase_distances
        phase_durations = distances.phase_durations
        phase_polylines = distances.phase_polylines
        pickup_to_driver_km = distances.pickup_to_driver_km
        road_polyline = distances.road_polyline
        gps_points_count = distances.gps_points_count
        route_quality = distances.route_quality
    except Exception as e:
        logger.error(f"Could not aggregate GPS data for ride {ride_id}: {e}", exc_info=True)

    # Legacy geometry side-table payload — same shape as the driver path so the
    # admin map modal reads both identically. mark_route_pending below upserts
    # the v2 queue fields onto this same row.
    route_payload = {
        "phase_distances": phase_distances,
        "phase_durations": phase_durations,
        "phase_polylines": phase_polylines,
        "road_polyline": road_polyline,
        "road_polyline_pickup": [],
        "gps_points_count": gps_points_count,
        "route_quality": route_quality,
        "save_status": "saved",
        "save_error": None,
        "computed_at": datetime.now(timezone.utc),
    }
    for attempt in range(1, 4):
        try:
            await db_supabase.update_one("ride_routes", {"ride_id": ride_id}, route_payload, upsert=True)
            route_geometry_status = "saved"
            route_geometry_error = None
            break
        except Exception as exc:
            route_geometry_error = str(exc)[:500]
            route_geometry_status = "failed"
            logger.error(
                "Could not persist ride_routes for ride %s (attempt %s/3): %s",
                ride_id,
                attempt,
                route_geometry_error,
                exc_info=True,
            )
            if attempt < 3:
                await asyncio.sleep(0.2 * attempt)

    # Geometry-only rides-row fields. Deliberately excludes distance_km, fare
    # fields, status, ride_metrics — see module docstring.
    geometry_fields: Dict[str, Any] = {
        "actual_distance_km": actual_distance_km,
        "pickup_to_driver_km": pickup_to_driver_km,
        "phase_distances": phase_distances,
        "phase_durations": phase_durations,
        "gps_points_count": gps_points_count,
        "route_quality": route_quality,
        "route_geometry_status": route_geometry_status,
        "route_geometry_error": route_geometry_error,
        "updated_at": datetime.now(timezone.utc),
    }
    try:
        await db_supabase.update_one("rides", {"id": ride_id, "status": "completed"}, geometry_fields)
    except Exception:
        # Older deployments may miss columns (PGRST204); the side-table write
        # and the finalizer queue below still carry the geometry.
        logger.error("settlement rides-row geometry update failed for ride %s", ride_id, exc_info=True)

    # Append-only SGI period-distance audit (P2/P3). record_ride_period_distances
    # is itself replay-safe and best-effort.
    try:
        _completed_at = ride.get("ride_completed_at") or ride.get("completed_at")
        if hasattr(_completed_at, "isoformat"):
            _completed_at = _completed_at.isoformat()
        await record_ride_period_distances(
            driver_id=driver_id,
            ride_id=ride_id,
            phases=[
                {
                    "period": 2,
                    "distance_km": phase_distances.get("navigating_to_pickup", pickup_to_driver_km),
                    # Period 2 starts on assignment (CLAUDE.md) — same fallback
                    # chain as the driver path.
                    "started_at": ride.get("assigned_at") or ride.get("driver_accepted_at"),
                    "ended_at": ride.get("ride_started_at"),
                },
                {
                    "period": 3,
                    "distance_km": phase_distances.get("trip_in_progress", actual_distance_km),
                    "started_at": ride.get("ride_started_at"),
                    "ended_at": _completed_at,
                },
            ],
        )
    except Exception:
        logger.error("period-distance audit failed for ride %s (settlement unaffected)", ride_id, exc_info=True)

    # Queue v2 finalization. These completion writers capture no driver-side
    # completion fix, so the tail is declared missing up front — the finalizer's
    # reconstruction/tail logic handles it from durable evidence.
    try:
        await mark_route_pending(ride_id, {"missing_tail": True, "rejection": f"no_completion_fix_{trigger}"})
    except Exception:
        logger.error("route finalization queue failed for ride_id=%s (trigger=%s)", ride_id, trigger, exc_info=True)

    _metric_inc("spinr_rides_geometry_settled_total", {"trigger": trigger, "outcome": "settled"})
    logger.info(
        "standalone geometry settlement done for ride %s (trigger=%s, gps_points=%d, status=%s)",
        ride_id,
        trigger,
        gps_points_count,
        route_geometry_status,
    )
    return True
