#!/usr/bin/env python3
"""Backfill OSRM road routes for all 224 imported rides.

Run from the backend server where OSRM_URL is accessible:

    cd backend
    python scripts/backfill_imported_ride_routes.py [--dry-run]

For each imported ride (legacy_import_metadata != '{}'):
  1. Calls OSRM /route to get the road-following route geometry
  2. Updates rides.distance_km with the OSRM road distance
  3. Stores the route polyline in rides.planned_route_polyline
  4. Optionally triggers route snapshot generation

Falls back to the public OSRM (OSRM_FALLBACK_URL) when
OSRM_URL is not set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

# Allow running as `python scripts/backfill_imported_ride_routes.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

try:
    from core.config import settings
except ImportError:
    from backend.core.config import settings

try:
    from settings_loader import get_app_settings
except ImportError:
    from backend.settings_loader import get_app_settings

try:
    import db_supabase
except ImportError:
    from backend import db_supabase

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_TIMEOUT_S = 10.0
_BATCH_DELAY_S = 0.3


async def _get_osrm_url() -> str:
    app_settings = await get_app_settings() or {}
    url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    if not url:
        url = (settings.OSRM_FALLBACK_URL or "").strip()
    if not url:
        raise RuntimeError("No OSRM URL configured. Set OSRM_URL env var or osrm_url in app_settings.")
    return url.rstrip("/")


async def _fetch_osrm_route(
    client: httpx.AsyncClient,
    osrm_url: str,
    pickup_lat: float,
    pickup_lng: float,
    dest_lat: float,
    dest_lng: float,
) -> tuple[float, list[list[float]]] | None:
    """Call OSRM /route and return (distance_km, [[lat,lng],...]) or None."""
    url = (
        f"{osrm_url}/route/v1/driving/{pickup_lng},{pickup_lat};{dest_lng},{dest_lat}?overview=full&geometries=geojson"
    )
    try:
        resp = await client.get(url, timeout=_TIMEOUT_S)
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            logger.warning(
                "OSRM non-Ok response for (%s,%s)->(%s,%s): %s",
                pickup_lat,
                pickup_lng,
                dest_lat,
                dest_lng,
                data.get("code"),
            )
            return None
        route = data["routes"][0]
        coords = route["geometry"]["coordinates"]  # [lng, lat] pairs
        latlon = [[c[1], c[0]] for c in coords]
        # Cap to 300 points
        if len(latlon) > 300:
            step = len(latlon) / 299
            sampled = [latlon[int(i * step)] for i in range(299)]
            if sampled[-1] != latlon[-1]:
                sampled.append(latlon[-1])
            latlon = sampled
        distance_km = round(route["distance"] / 1000, 2)
        return distance_km, latlon
    except Exception as e:
        logger.error("OSRM request failed for (%s,%s)->(%s,%s): %s", pickup_lat, pickup_lng, dest_lat, dest_lng, e)
        return None


async def main(dry_run: bool = False) -> None:
    osrm_url = await _get_osrm_url()
    logger.info("Using OSRM at: %s", osrm_url)

    # Fetch all imported rides
    rides = await db_supabase.get_rows(
        "rides",
        {"legacy_import_metadata": {"$ne": "{}"}},
        columns="id,pickup_lat,pickup_lng,dropoff_lat,dropoff_lng,distance_km,planned_route_polyline",
        limit=500,
    )
    if not rides:
        logger.info("No imported rides found.")
        return

    logger.info("Found %d imported rides to backfill", len(rides))

    # Deduplicate by coordinate pairs
    coord_key_map: dict[str, tuple[float, float, float, float]] = {}
    ride_keys: dict[str, str] = {}
    for r in rides:
        plat = r.get("pickup_lat")
        plng = r.get("pickup_lng")
        dlat = r.get("dropoff_lat")
        dlng = r.get("dropoff_lng")
        if not all(isinstance(v, (int, float)) for v in [plat, plng, dlat, dlng]):
            continue
        key = f"{plat:.4f},{plng:.4f},{dlat:.4f},{dlng:.4f}"
        coord_key_map[key] = (plat, plng, dlat, dlng)
        ride_keys[r["id"]] = key

    logger.info("Unique coordinate pairs: %d", len(coord_key_map))

    # Fetch routes
    route_cache: dict[str, tuple[float, list[list[float]]] | None] = {}
    async with httpx.AsyncClient() as client:
        for i, (key, (plat, plng, dlat, dlng)) in enumerate(coord_key_map.items()):
            result = await _fetch_osrm_route(client, osrm_url, plat, plng, dlat, dlng)
            route_cache[key] = result
            if (i + 1) % 20 == 0:
                logger.info("  Progress: %d/%d routes fetched", i + 1, len(coord_key_map))
            await asyncio.sleep(_BATCH_DELAY_S)

    success_count = sum(1 for v in route_cache.values() if v is not None)
    logger.info("Fetched %d/%d routes successfully", success_count, len(route_cache))

    if dry_run:
        logger.info("[DRY RUN] Would update %d rides. Exiting.", len(rides))
        for r in rides[:5]:
            key = ride_keys.get(r["id"])
            if key and route_cache.get(key):
                dist, coords = route_cache[key]
                logger.info("  Ride %s: %.2f km, %d route points", r["id"][:8], dist, len(coords))
        return

    # Update rides
    updated = 0
    for r in rides:
        key = ride_keys.get(r["id"])
        if not key:
            continue
        result = route_cache.get(key)
        if result is None:
            continue
        dist_km, polyline = result
        update_data: dict = {"distance_km": dist_km}
        if not r.get("planned_route_polyline"):
            # [[lat, lng], …] — the shape migration 100 defines and every
            # consumer reads (schemas.py List[List[float]], the apps'
            # [number, number][] stores, admin's p[0]/p[1] indexing, and
            # validCoordinate() in shared/utils/routeSegments.ts, which
            # rejects a segment outright if its points aren't arrays).
            # Writing {lat, lng} objects here silently blanked the route
            # line on every ride-detail map — see migration 313.
            update_data["planned_route_polyline"] = json.dumps([[p[0], p[1]] for p in polyline])
        try:
            await db_supabase.update_one("rides", r["id"], update_data)
            updated += 1
        except Exception as e:
            logger.error("Failed to update ride %s: %s", r["id"][:8], e)

    logger.info("Updated %d/%d imported rides with OSRM road distances", updated, len(rides))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
