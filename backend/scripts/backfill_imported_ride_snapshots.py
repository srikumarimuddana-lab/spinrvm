#!/usr/bin/env python3
"""Regenerate route snapshot images for imported rides using Google Static Maps.

Run from the backend server where SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
and the Google Maps API key (in the settings table) are available:

    cd backend
    python scripts/backfill_imported_ride_snapshots.py [--apply] [--force]

Dry run is the default (matches every other script in this file's family —
see backfill_legacy_driver_sin_dob.py, backfill_statement_totals.py, etc.);
pass --apply to actually render/upload/write.

For each imported ride (legacy_import_metadata IS NOT NULL):
  1. Renders a PNG via Google Static Maps API with the OSRM route polyline
     drawn as an orange→red gradient (same style as production snapshots)
  2. Uploads the PNG to Supabase Storage (ride-snapshots bucket)
  3. Updates rides.route_snapshot_url with the public URL

Reads are paged (PAGE_SIZE) rather than a single capped select — an
unbounded single read silently caps at the requested limit and would leave
rides beyond it un-processed with a summary line that looks complete. If
more rows remain beyond a --limit, the run logs a warning and the caller
should re-run to finish.

Options:
  --apply     Actually render/upload/write (default: dry run)
  --force     Re-generate even if route_snapshot_url is already set
  --limit N   Process at most N rides (default: all)
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

try:
    from supabase_client import supabase
except ImportError:
    from backend.supabase_client import supabase

try:
    from utils.route_snapshot import (
        normalize_polyline_points,
        render_ride_snapshot,
        render_ride_snapshot_google,
    )
except ImportError:
    from backend.utils.route_snapshot import (
        normalize_polyline_points,
        render_ride_snapshot,
        render_ride_snapshot_google,
    )

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_BATCH_DELAY_S = 0.5
_BUCKET = "ride-snapshots"
_PAGE_SIZE = 500


async def _fetch_rides_needing_snapshots(filters: dict, limit: int | None) -> tuple[list[dict], bool]:
    """Page through candidate rides. Returns (rides, has_more)."""
    rides: list[dict] = []
    offset = 0
    while True:
        page = (
            await db_supabase.get_rows(
                "rides",
                filters,
                columns="id,pickup_lat,pickup_lng,dropoff_lat,dropoff_lng,planned_route_polyline",
                limit=_PAGE_SIZE,
                offset=offset,
            )
            or []
        )
        rides.extend(page)
        if len(page) < _PAGE_SIZE:
            return rides, False
        if limit and len(rides) >= limit:
            return rides[:limit], True
        offset += _PAGE_SIZE


async def main(dry_run: bool = True, force: bool = False, limit: int | None = None) -> None:
    app_settings = await get_app_settings() or {}
    gmap_key = (app_settings.get("google_maps_api_key") or "").strip()

    if not gmap_key:
        logger.warning("No Google Maps API key in settings table — will use OSM/staticmap fallback")
    else:
        logger.info("Google Maps API key found, will use Google Static Maps renderer")

    filters = {"legacy_import_metadata": {"$notnull": True}}
    if not force:
        filters["route_snapshot_url"] = None

    rides, has_more = await _fetch_rides_needing_snapshots(filters, limit)
    if not rides:
        logger.info("No rides to process (all have snapshots or no imported rides found).")
        return

    logger.info("Found %d rides to generate snapshots for", len(rides))
    if has_more:
        logger.warning("more rides remain beyond --limit; re-run to continue")

    if dry_run:
        for r in rides:
            logger.info("  [DRY RUN] Would generate snapshot for ride %s", r["id"])
        return

    loop = asyncio.get_event_loop()
    success = 0
    failed = 0
    base_url = (settings.SUPABASE_URL or "").rstrip("/")

    for i, ride in enumerate(rides):
        ride_id = ride["id"]
        pickup_lat = ride.get("pickup_lat")
        pickup_lng = ride.get("pickup_lng")
        dropoff_lat = ride.get("dropoff_lat")
        dropoff_lng = ride.get("dropoff_lng")

        if not all(isinstance(v, (int, float)) for v in [pickup_lat, pickup_lng, dropoff_lat, dropoff_lng]):
            logger.warning("Skipping ride %s: missing coordinates", ride_id)
            failed += 1
            continue

        route_polyline = normalize_polyline_points(ride.get("planned_route_polyline"))

        png_bytes = None

        if gmap_key:
            try:
                png_bytes = await render_ride_snapshot_google(
                    api_key=gmap_key,
                    pickup_lat=float(pickup_lat),
                    pickup_lng=float(pickup_lng),
                    dropoff_lat=float(dropoff_lat),
                    dropoff_lng=float(dropoff_lng),
                    route_polyline=route_polyline,
                )
            except Exception as exc:
                logger.error("Google render failed for ride %s: %s", ride_id, exc)

        if not png_bytes:
            try:
                png_bytes = await loop.run_in_executor(
                    None,
                    functools.partial(
                        render_ride_snapshot,
                        pickup_lat=float(pickup_lat),
                        pickup_lng=float(pickup_lng),
                        dropoff_lat=float(dropoff_lat),
                        dropoff_lng=float(dropoff_lng),
                        route_polyline=route_polyline,
                    ),
                )
            except Exception as exc:
                logger.error("OSM render failed for ride %s: %s", ride_id, exc)

        if not png_bytes:
            logger.error("No snapshot generated for ride %s", ride_id)
            failed += 1
            continue

        storage_path = f"ride_{ride_id}.png"
        try:
            await loop.run_in_executor(
                None,
                functools.partial(
                    supabase.storage.from_(_BUCKET).upload,
                    path=storage_path,
                    file=png_bytes,
                    file_options={
                        "content-type": "image/png",
                        "upsert": "true",
                        "cache-control": "31536000",
                    },
                ),
            )
        except Exception as exc:
            logger.error("Upload failed for ride %s: %s", ride_id, exc)
            failed += 1
            continue

        digest = hashlib.sha256(png_bytes).hexdigest()[:12]
        url = f"{base_url}/storage/v1/object/public/{_BUCKET}/{storage_path}?v={digest}"

        try:
            await db_supabase.update_one("rides", {"id": ride_id}, {"route_snapshot_url": url})
        except Exception as exc:
            logger.error("DB update failed for ride %s: %s", ride_id, exc)
            failed += 1
            continue

        success += 1
        if (i + 1) % 10 == 0:
            logger.info("  Progress: %d/%d processed (%d ok, %d failed)", i + 1, len(rides), success, failed)

        await asyncio.sleep(_BATCH_DELAY_S)

    logger.info("Done: %d succeeded, %d failed out of %d total", success, failed, len(rides))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill route snapshots for imported rides", formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="Actually render/upload/write (default: dry run)")
    parser.add_argument("--force", action="store_true", help="Re-generate even if snapshot exists")
    parser.add_argument("--limit", type=int, default=None, help="Max rides to process")
    args = parser.parse_args()
    asyncio.run(main(dry_run=not args.apply, force=args.force, limit=args.limit))
