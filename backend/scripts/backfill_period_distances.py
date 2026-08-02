#!/usr/bin/env python3
"""Backfill driver_period_distances (migration 249) for completed rides that
predate the live writer (record_ride_period_distances, added to
routes/drivers/ride_complete.py on 2026-07-28).

Source data: rides.ride_metrics.phases, already stored on every completed
ride regardless of when it completed. Only backfills rides where GPS-measured
distance (`actual_distance_km`) is present for a phase — a ride with only
`estimated_distance_km` (the pre-trip route quote) is deliberately skipped,
never used as a stand-in. driver_period_distances exists specifically to
capture GPS-measured driven distance for insurer billing/audit; writing a
pre-trip estimate into it as if it were GPS-measured would misstate the
provenance of an insurer-facing document. Rides that only have an estimate
stay uncovered — same standard the live writer already applies (it only
records phase_distances computed from GPS breadcrumbs).

    # 1. See what would be written. Reads only — no writes.
    python backend/scripts/backfill_period_distances.py

    # 2. Write it.
    python backend/scripts/backfill_period_distances.py --apply

Dry run is the default, deliberately: this writes to an append-only
regulatory audit table (CLAUDE.md: never delete or mutate period rows).

Environment — the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Safety properties:
  * Only considers rides with status='completed' AND a non-null driver_id —
    a completed ride with no driver can't be attributed to anyone.
  * Only backfills a (ride_id, period) pair with a real actual_distance_km —
    never falls back to estimated_distance_km.
  * record_ride_period_distances() itself is replay-safe (checks existing
    rows per ride, and the (ride_id, period) unique index is the atomic
    backstop) — running this script twice, or running it while the live
    writer is also active for new rides, is safe.
  * A failure on one ride never stops the rest; failures are counted and
    logged, never silently swallowed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backfill_period_distances")

_ROW_LIMIT = 10000


def _phase_distance_km(ride_metrics: dict, phase_key: str) -> float | None:
    """GPS-measured distance for one phase, or None if only an estimate (or
    nothing) is on file. Deliberately never falls back to
    estimated_distance_km — see module docstring."""
    phases = (ride_metrics or {}).get("phases") or {}
    phase = phases.get(phase_key) or {}
    value = phase.get("actual_distance_km")
    if value is None:
        return None
    try:
        km = float(value)
    except (TypeError, ValueError):
        return None
    return km if km >= 0 else None


async def _main(apply_changes: bool, before: str | None) -> int:
    import db_supabase
    from utils.period_distance_audit import record_ride_period_distances

    filters: dict = {"status": "completed", "driver_id": {"$notnull": True}}
    if before:
        filters["ride_completed_at"] = {"$lt": before}

    rides = await db_supabase.get_rows(
        "rides",
        filters,
        columns="id,driver_id,ride_metrics,assigned_at,driver_accepted_at,ride_started_at,ride_completed_at",
        limit=_ROW_LIMIT,
    )
    logger.info("scanned %s completed ride(s)%s", len(rides), f" before {before}" if before else "")

    stats = {"scanned": len(rides), "no_gps_distance": 0, "would_write": 0, "written": 0, "failed": 0}

    for ride in rides:
        ride_id = ride.get("id")
        driver_id = ride.get("driver_id")
        ride_metrics = ride.get("ride_metrics") or {}

        period2_km = _phase_distance_km(ride_metrics, "navigating_to_pickup")
        period3_km = _phase_distance_km(ride_metrics, "trip_in_progress")
        if period2_km is None and period3_km is None:
            stats["no_gps_distance"] += 1
            continue

        phases = []
        if period2_km is not None:
            phases.append(
                {
                    "period": 2,
                    "distance_km": period2_km,
                    "started_at": ride.get("assigned_at") or ride.get("driver_accepted_at"),
                    "ended_at": ride.get("ride_started_at"),
                    "source": "gps_measured_backfill",
                }
            )
        if period3_km is not None:
            phases.append(
                {
                    "period": 3,
                    "distance_km": period3_km,
                    "started_at": ride.get("ride_started_at"),
                    "ended_at": ride.get("ride_completed_at"),
                    "source": "gps_measured_backfill",
                }
            )

        stats["would_write"] += len(phases)
        if not apply_changes:
            continue

        try:
            written = await record_ride_period_distances(driver_id=driver_id, ride_id=ride_id, phases=phases)
            stats["written"] += written
        except Exception:
            stats["failed"] += 1
            logger.error("backfill failed for ride %s", ride_id, exc_info=True)

    logger.info(
        "done: scanned=%s no_gps_distance=%s would_write=%s written=%s failed=%s%s",
        stats["scanned"],
        stats["no_gps_distance"],
        stats["would_write"],
        stats["written"],
        stats["failed"],
        "" if apply_changes else " (dry run — pass --apply to write)",
    )
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually write rows (default: dry run, reads only)")
    parser.add_argument(
        "--before",
        default=None,
        help="Only backfill rides completed before this ISO timestamp (default: all completed rides)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.apply, args.before)))
