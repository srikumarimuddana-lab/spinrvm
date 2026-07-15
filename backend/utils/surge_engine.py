"""
Automated surge pricing engine.

Calculates demand/supply ratio per service area and maps it to a surge
multiplier tier. Runs as a background task every 2 minutes, updating
service_areas.surge_multiplier for areas where surge_source == 'auto'.
"""

import asyncio
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from loguru import logger

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from db import db
    from geo_utils import get_service_area_polygon, point_in_polygon
    from utils.driver_presence import present_driver_ids
    from utils.metrics import inc as _metric_inc
    from utils.redis_client import redis_set_nx
except ImportError:
    from ..db import db
    from ..geo_utils import get_service_area_polygon, point_in_polygon
    from .driver_presence import present_driver_ids
    from .metrics import inc as _metric_inc
    from .redis_client import redis_set_nx

# Per-area supply/demand fetch cap. Surge = demand/supply, a regulated rider-
# facing price; if a fetch hits this cap the count is TRUNCATED and the
# multiplier is wrong (supply undercount → over-price). Raised from 500 and made
# loud (metric + warning below) so truncation can never silently mis-price.
# The real fix is a server-side spatial count (PostGIS ST_Covers + GIST index);
# tracked as ACTION_ITEMS D1.
_SURGE_FETCH_CAP = 5000

# D1: enable the PostGIS server-side supply count (migration 170). Off by default
# so applying the migration changes nothing until it's been rehearsed on staging;
# flip SURGE_SPATIAL_COUNT=true to activate. The path is fallback-safe regardless.
_SURGE_SPATIAL_COUNT = os.environ.get("SURGE_SPATIAL_COUNT", "").strip().lower() in ("1", "true", "yes", "on")

# ── Surge tier mapping ───────────────────────────────────────────────
# Maps demand/supply ratio to multiplier. Thresholds are tuned for a
# mid-size city (Saskatchewan-scale). Can be adjusted via admin settings
# in a future phase.

SURGE_TIERS = [
    (0.5, 1.0),  # ratio < 0.5  → normal
    (0.8, 1.25),  # 0.5 ≤ ratio < 0.8
    (1.2, 1.5),  # 0.8 ≤ ratio < 1.2
    (2.0, 1.75),  # 1.2 ≤ ratio < 2.0
    (3.0, 2.0),  # 2.0 ≤ ratio < 3.0
]
SURGE_CAP = 2.5  # ratio ≥ 3.0 → capped at 2.5x

# How far back to look for active ride demand (minutes)
DEMAND_WINDOW_MINUTES = 10

# Background loop interval (seconds)
RECALC_INTERVAL_SECONDS = 120


def ratio_to_multiplier(ratio: float) -> float:
    """Map a demand/supply ratio to a surge multiplier tier."""
    for threshold, multiplier in SURGE_TIERS:
        if ratio < threshold:
            return multiplier
    return SURGE_CAP


async def _count_demand_in_area(area_id: str) -> int:
    """Count active/recent ride requests in a service area."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=DEMAND_WINDOW_MINUTES)).isoformat()
    try:
        rides = await db.get_rows(
            "rides",
            {
                "service_area_id": area_id,
                "ride_requested_at": {"$gte": cutoff},
            },
            limit=_SURGE_FETCH_CAP,
            columns="id,status",
        )
        if len(rides) >= _SURGE_FETCH_CAP:
            _metric_inc("spinr_surge_fetch_cap_hit_total", {"kind": "demand"})
            logger.error(
                f"Surge: demand fetch hit the {_SURGE_FETCH_CAP}-row cap for area {area_id} — "
                f"demand is TRUNCATED and the surge multiplier may be under-stated. "
                f"Move to a server-side spatial/aggregate count (D1)."
            )
        # Count rides that are still active or very recently requested
        active_statuses = {
            "searching",
            "driver_assigned",
            "driver_accepted",
            "driver_arrived",
            "in_progress",
        }
        return sum(1 for r in rides if r.get("status") in active_statuses)
    except Exception as e:
        logger.error(f"Surge: failed to count demand for area {area_id}: {e}", exc_info=True)
        return 0


async def _count_supply_spatial(poly: List[Dict[str, float]]) -> int:
    """PostGIS supply count via the drivers_available_in_polygon RPC (D1).

    Builds a GeoJSON polygon with [lng, lat] ordering (what ST_GeomFromGeoJSON
    expects — we construct it ourselves so there's no axis-swap risk), runs the
    SECURITY DEFINER spatial lookup (index-only, no row cap), then applies the
    Redis presence filter in Python so ghost-online drivers are excluded — same
    semantics as the Python scan, without the truncation cap.
    """
    ring = [[p["lng"], p["lat"]] for p in poly]
    if ring[0] != ring[-1]:
        ring.append(ring[0])  # GeoJSON rings must be closed
    geojson = json.dumps({"type": "Polygon", "coordinates": [ring]})
    rows = await db.rpc("drivers_available_in_polygon", {"p_polygon_geojson": geojson}) or []
    driver_ids = [r["driver_id"] for r in rows if r.get("driver_id")]
    if not driver_ids:
        return 0
    try:
        present = await present_driver_ids(driver_ids)
        if present:
            driver_ids = [d for d in driver_ids if d in present]
    except Exception as exc:
        logger.warning(f"Surge: presence filter failed (spatial), using DB state: {exc}")
    return len(driver_ids)


async def _count_supply_in_area(area: Dict[str, Any]) -> int:
    """Count online+available drivers within a service area polygon."""
    poly = get_service_area_polygon(area)
    if not poly:
        return 0

    # D1: server-side spatial count (no row cap, index-only). Flag-gated and
    # fully fallback-safe — on any error (flag off, migration 170 not applied,
    # extension missing, RPC issue) we fall through to the Python scan below, so
    # surge never breaks.
    if _SURGE_SPATIAL_COUNT:
        try:
            return await _count_supply_spatial(poly)
        except Exception as exc:
            logger.warning(
                f"Surge: spatial supply count failed for area {area.get('id')}, falling back to Python scan: {exc}",
                exc_info=False,
            )

    try:
        drivers = await db.get_rows(
            "drivers",
            {"is_online": True, "is_available": True},
            limit=_SURGE_FETCH_CAP,
            columns="id,user_id,lat,lng",
        )
        if len(drivers) >= _SURGE_FETCH_CAP:
            _metric_inc("spinr_surge_fetch_cap_hit_total", {"kind": "supply"})
            logger.error(
                f"Surge: supply fetch hit the {_SURGE_FETCH_CAP}-row cap for area {area.get('id')} — "
                f"supply is TRUNCATED and the surge multiplier may be OVER-stated (a regulated price). "
                f"Move to a server-side spatial count (PostGIS ST_Covers + GIST index, D1)."
            )

        # Presence filter: ghost-online drivers (app force-killed, phone dead)
        # shouldn't count as "supply" or we'd compute a lower surge than the
        # real market needs. Safe-fallback: if presence lookup fails, trust
        # the DB rows — worst case surge is slightly under-pricing for one
        # tick, never over-pricing based on a Redis outage.
        try:
            driver_ids = [d["id"] for d in drivers if d.get("id")]
            present = await present_driver_ids(driver_ids) if driver_ids else set()
            if present:
                drivers = [d for d in drivers if d["id"] in present]
        except Exception as exc:
            logger.warning(f"Surge: presence filter failed, using DB state: {exc}")

        count = 0
        for d in drivers:
            d_lat = d.get("lat")
            d_lng = d.get("lng")
            if d_lat and d_lng and d.get("user_id"):
                if point_in_polygon(d_lat, d_lng, poly):
                    count += 1
        return count
    except Exception as e:
        logger.error(
            f"Surge: failed to count supply for area {area.get('id')}: {e}",
            exc_info=True,
        )
        return 0


async def calculate_surge_for_area(area: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate surge metrics for a single service area.

    Returns dict with demand, supply, ratio, and recommended multiplier.
    """
    area_id = area["id"]
    demand = await _count_demand_in_area(area_id)
    supply = await _count_supply_in_area(area)
    ratio = demand / max(supply, 1)
    multiplier = ratio_to_multiplier(ratio)

    return {
        "area_id": area_id,
        "demand_count": demand,
        "supply_count": supply,
        "ratio": round(ratio, 2),
        "multiplier": multiplier,
    }


async def recalculate_all_surges() -> List[Dict[str, Any]]:
    """
    Recalculate surge for all surge-enabled active service areas.

    Only updates areas where surge_source == 'auto' (or not set).
    Areas with surge_source == 'manual' are skipped — the admin's
    override takes precedence until they reset it to auto.

    surge_enabled is filtered in the DB query (not just in Python) so a
    deployment where no area has the surge toggle on costs one empty
    response per tick instead of a full service_areas read every 2 min
    (Supabase egress).
    """
    results = []

    try:
        areas = await db.get_rows("service_areas", {"is_active": True, "surge_enabled": True}, limit=100)
    except Exception as e:
        original = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
        logger.error(f"Surge: failed to fetch service areas: {e} | original={original}")
        return results

    for area in areas:
        # Skip sub-areas (airports) — surge is managed at parent level
        if area.get("parent_service_area_id"):
            continue

        # Skip manually overridden areas
        surge_source = area.get("surge_source", "auto")
        if surge_source == "manual":
            continue

        # Per-area admin master gate: never auto-activate surge for an area the
        # operator hasn't explicitly enabled (surge_enabled defaults FALSE).
        # The DB query above already filters on surge_enabled; this guard is a
        # backstop so a caller passing unfiltered rows can never auto-activate
        # a disabled area. The fare paths apply the same gate.
        if not area.get("surge_enabled", False):
            continue

        try:
            metrics = await calculate_surge_for_area(area)
            new_multiplier = metrics["multiplier"]
            old_multiplier = area.get("surge_multiplier", 1.0)

            # Only update DB if multiplier changed
            if new_multiplier != old_multiplier:
                await db.update_one(
                    "service_areas",
                    {"id": area["id"]},
                    {
                        "surge_multiplier": new_multiplier,
                        "surge_active": new_multiplier > 1.0,
                    },
                )
                logger.info(
                    f"Surge: area '{area.get('name', area['id'])}' "
                    f"{old_multiplier}x → {new_multiplier}x "
                    f"(demand={metrics['demand_count']}, supply={metrics['supply_count']}, "
                    f"ratio={metrics['ratio']})"
                )

            # Log to surge_pricing history
            await db.insert_one(
                "surge_pricing",
                {
                    "id": str(uuid.uuid4()),
                    "service_area_id": area["id"],
                    "multiplier": new_multiplier,
                    "demand_count": metrics["demand_count"],
                    "supply_count": metrics["supply_count"],
                    "ratio": metrics["ratio"],
                    "source": "auto",
                    "is_active": new_multiplier > 1.0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            results.append(metrics)
        except Exception as e:
            logger.error(f"Surge: failed to update area {area.get('id')}: {e}", exc_info=True)

    return results


async def get_surge_status() -> List[Dict[str, Any]]:
    """
    Get current surge status for all active service areas.
    Used by the admin dashboard to display live surge info.
    """
    areas = await db.get_rows("service_areas", {"is_active": True}, limit=100)

    statuses = []
    for area in areas:
        if area.get("parent_service_area_id"):
            continue

        # Calculate live demand/supply for each area
        demand = await _count_demand_in_area(area["id"])
        supply = await _count_supply_in_area(area)
        ratio = round(demand / max(supply, 1), 2)

        # Per-area surge master toggle. Report the EFFECTIVE surge the fare
        # paths would apply: when surge_enabled is off, surge never applies, so
        # a parked multiplier / stale surge_active flag must read as 1.0× /
        # inactive here too — otherwise the admin status view advertises surge
        # that booking never charges. surge_enabled is surfaced so the dashboard
        # can show the toggle state directly.
        surge_enabled = bool(area.get("surge_enabled", False))
        surge_active = surge_enabled and bool(area.get("surge_active", False))
        multiplier = area.get("surge_multiplier", 1.0) if surge_active else 1.0

        statuses.append(
            {
                "area_id": area["id"],
                "name": area.get("name", ""),
                "city": area.get("city", ""),
                "surge_enabled": surge_enabled,
                "multiplier": multiplier,
                "surge_active": surge_active,
                "source": area.get("surge_source", "auto"),
                "demand_count": demand,
                "supply_count": supply,
                "ratio": ratio,
                "last_updated": area.get("updated_at"),
            }
        )
    return statuses


async def surge_recalculation_loop():
    """Background loop that recalculates surge every RECALC_INTERVAL_SECONDS."""
    logger.info(f"Surge engine started (interval={RECALC_INTERVAL_SECONDS}s)")
    while True:
        # Single-writer across replicas. Without this gate every replica runs
        # the tick and inserts a surge_pricing history row → N duplicate rows
        # per area per interval. Lock on a per-interval wall-clock bucket so
        # exactly one replica handles each window while preserving the ~2min
        # cadence (fresh bucket key each interval; TTL 2× absorbs jitter/skew).
        # Edge case: if a replica's jittered sleep lands its next iteration in
        # the bucket it already holds, it's denied and skips that one window —
        # self-healing (next window is a fresh bucket) and covered by peers in a
        # multi-replica deployment.
        bucket = int(datetime.now(timezone.utc).timestamp() // RECALC_INTERVAL_SECONDS)
        lock_ttl = int(RECALC_INTERVAL_SECONDS * 2)
        try:
            is_leader = await redis_set_nx(f"spinr:surge_engine:lock:{bucket}", "1", lock_ttl)
        except Exception as _lock_err:
            # Redis unavailable (dev in-memory fallback / outage): proceed. Dev is
            # single-process; a prod Redis outage degrades to per-replica recompute
            # and self-heals once Redis returns. Mirrors scheduled_rides' degrade.
            logger.warning(f"Surge: leader lock unavailable ({_lock_err}), proceeding without lock")
            is_leader = True

        # B-P3-2: ±10% per-tick jitter so replicas don't all flip surge state on
        # the same wall-clock tick (rider apps would receive a synchronised
        # price-change notification storm).
        delta = RECALC_INTERVAL_SECONDS * 0.1
        sleep_seconds = RECALC_INTERVAL_SECONDS + random.uniform(-delta, delta)

        if not is_leader:
            _record_heartbeat("surge_engine (2min)")
            await asyncio.sleep(sleep_seconds)
            continue

        try:
            results = await recalculate_all_surges()
            if results:
                active = sum(1 for r in results if r["multiplier"] > 1.0)
                logger.debug(f"Surge recalc complete: {len(results)} areas, {active} surging")
        except Exception as e:
            logger.error(f"Surge recalculation loop error: {e}")
        _record_heartbeat("surge_engine (2min)")
        await asyncio.sleep(sleep_seconds)
