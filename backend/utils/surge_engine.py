"""
Automated surge pricing engine.

Calculates demand/supply ratio per service area and maps it to a surge
multiplier tier. Runs as a background task every 2 minutes, updating
service_areas.surge_multiplier for areas where surge_source == 'auto'.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

from loguru import logger

try:
    from db import db
    from geo_utils import get_service_area_polygon, point_in_polygon
except ImportError:
    from ..db import db
    from ..geo_utils import get_service_area_polygon, point_in_polygon

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
    cutoff = (datetime.utcnow() - timedelta(minutes=DEMAND_WINDOW_MINUTES)).isoformat()
    try:
        rides = await db.get_rows(
            "rides",
            {
                "service_area_id": area_id,
                "ride_requested_at": {"$gte": cutoff},
            },
            limit=500,
        )
        # Count rides that are still active or very recently requested
        active_statuses = {"searching", "driver_assigned", "driver_en_route"}
        return sum(1 for r in rides if r.get("status") in active_statuses)
    except Exception as e:
        logger.warning(f"Surge: failed to count demand for area {area_id}: {e}")
        return 0


async def _count_supply_in_area(area: Dict[str, Any]) -> int:
    """Count online+available drivers within a service area polygon."""
    poly = get_service_area_polygon(area)
    if not poly:
        return 0

    try:
        drivers = await db.get_rows("drivers", {"is_online": True, "is_available": True}, limit=500)

        count = 0
        for d in drivers:
            d_lat = d.get("lat")
            d_lng = d.get("lng")
            if d_lat and d_lng and d.get("user_id"):
                if point_in_polygon(d_lat, d_lng, poly):
                    count += 1
        return count
    except Exception as e:
        logger.warning(f"Surge: failed to count supply for area {area.get('id')}: {e}")
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
    Recalculate surge for all active service areas.

    Only updates areas where surge_source == 'auto' (or not set).
    Areas with surge_source == 'manual' are skipped — the admin's
    override takes precedence until they reset it to auto.
    """
    results = []

    try:
        areas = await db.get_rows("service_areas", {"is_active": True}, limit=100)
    except Exception as e:
        logger.error(f"Surge: failed to fetch service areas: {e}")
        return results

    for area in areas:
        # Skip sub-areas (airports) — surge is managed at parent level
        if area.get("parent_service_area_id"):
            continue

        # Skip manually overridden areas
        surge_source = area.get("surge_source", "auto")
        if surge_source == "manual":
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
                    "created_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat(),
                }
            )

            results.append(metrics)
        except Exception as e:
            logger.warning(f"Surge: failed to update area {area.get('id')}: {e}")

    return results


async def get_surge_status() -> List[Dict[str, Any]]:
    """
    Get current surge status for all active service areas.
    Used by the admin dashboard to display live surge info.
    """
    try:
        areas = await db.get_rows("service_areas", {"is_active": True}, limit=100)
    except Exception as e:
        logger.error(f"Surge: failed to fetch areas for status: {e}")
        return []

    statuses = []
    for area in areas:
        if area.get("parent_service_area_id"):
            continue

        # Calculate live demand/supply for each area
        demand = await _count_demand_in_area(area["id"])
        supply = await _count_supply_in_area(area)
        ratio = round(demand / max(supply, 1), 2)

        statuses.append(
            {
                "area_id": area["id"],
                "name": area.get("name", ""),
                "city": area.get("city", ""),
                "multiplier": area.get("surge_multiplier", 1.0),
                "surge_active": area.get("surge_active", False),
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
        try:
            results = await recalculate_all_surges()
            if results:
                active = sum(1 for r in results if r["multiplier"] > 1.0)
                logger.debug(f"Surge recalc complete: {len(results)} areas, {active} surging")
        except Exception as e:
            logger.error(f"Surge recalculation loop error: {e}")
        await asyncio.sleep(RECALC_INTERVAL_SECONDS)
