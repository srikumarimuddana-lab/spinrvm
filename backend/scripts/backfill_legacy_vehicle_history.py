#!/usr/bin/env python3
"""Backfill driver_vehicle_history for legacy-imported drivers from vehicle_details.csv.

Thin CLI over ``services/driver_import_service.py``'s
``plan_legacy_vehicle_history_backfill``/``apply_legacy_vehicle_history_backfill``.
Source is two files from the raw MongoDB export of the old app (``Mongo.zip``):
``vehicle_details.csv`` (vehicle make/model/colour/year/plate/VIN, keyed by a
Mongo ObjectId ``driver_id``) and ``drivers.csv`` (the same export's driver
collection, used only to resolve that ObjectId to a phone number -- the same
crosswalk the SIN/DOB backfill already established as 100% joinable). Closes
the Oct 30 checklist's item #4 (7-year driver/vehicle-linkage regulatory
retention gap) -- see docs/runbooks/legacy-migration-playbook.md.

    # 1. See what would change. Reads only -- no writes. (default)
    python backend/scripts/backfill_legacy_vehicle_history.py \\
        --vehicle-details-csv Mongo/vehicle_details.csv --drivers-csv Mongo/drivers.csv

    # 2. Write history rows for the matched drivers.
    python backend/scripts/backfill_legacy_vehicle_history.py \\
        --vehicle-details-csv Mongo/vehicle_details.csv --drivers-csv Mongo/drivers.csv --apply

Environment -- the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Safety, enforced in the service layer (not repeated here):
  - only touches drivers already tagged with this repo's own
    ``legacy_saskatoon_driver_import`` source in ``legacy_import_metadata``;
    a phone coincidence can never touch an organic driver's vehicle history.
  - append-only -- never updates or deletes an existing driver_vehicle_history
    row, matching that table's own migration-157 invariant. Idempotent across
    re-runs (skips any (driver_id, field, created_at, new_value) tuple already
    on file); see the service module's own comment for why a concurrent
    double-run is at worst a harmless duplicate, not data loss.
  - a driver with more than one vehicle_details.csv row gets a real
    before/after chain reconstructed (sorted by the legacy row's own
    created_at, never import time) -- a history row is only written when a
    field's value actually changed from the previously-known value.

PIPEDA: this reads plate/VIN/make/model from a local CSV. Neither this script
nor the service it calls ever logs, prints, or returns a raw plate/VIN value --
only old_driver_id/old_vehicle_id/field-name/generic-message per report line.

Rollback for an applied run: this backfill is append-only, so there is no
existing value it could have clobbered. To revert, delete the
driver_vehicle_history rows this run inserted -- every row's driver_id/field/
created_at is logged per report line above, which is enough to identify them
precisely (created_at for a backfilled row is the *legacy* event time, always
well before this script's own run date, so it cannot collide with a real
live edit's history row).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backfill_legacy_vehicle_history")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--vehicle-details-csv", required=True, type=Path, help="path to the export's vehicle_details.csv"
    )
    parser.add_argument("--drivers-csv", required=True, type=Path, help="path to the export's drivers.csv")
    parser.add_argument("--apply", action="store_true", help="write to the DB (default: dry run)")
    args = parser.parse_args()

    try:
        from services import driver_import_service as svc
    except ImportError:  # pragma: no cover - CLI convenience
        from backend.services import driver_import_service as svc  # type: ignore

    # read_mongo_export_csv, NOT read_csv -- see driver_import_service.py's
    # own docstring on why: read_csv's header normalization (built for the
    # bespoke Saskatoon driver CSV) mangles "_id" -> "id" (breaking this
    # phone crosswalk) and vehicle_details.csv's "name" column (make) into
    # "full_name" (an alias meant for a person's name).
    vehicle_rows = svc.read_mongo_export_csv(args.vehicle_details_csv)
    driver_rows = svc.read_mongo_export_csv(args.drivers_csv)

    plan = svc.plan_legacy_vehicle_history_backfill(vehicle_rows, driver_rows)
    svc.print_vehicle_history_report(plan, dry_run=not args.apply)

    if plan.errors:
        logger.error("refusing to apply: %d validation error(s) above", len(plan.errors))
        return 1

    if not args.apply:
        logger.info("dry run only -- pass --apply to write %d history row(s)", len(plan.rows_to_insert))
        return 0

    if not plan.rows_to_insert:
        logger.info("nothing to apply")
        return 0

    svc.apply_legacy_vehicle_history_backfill(plan)
    logger.info("applied %d history row(s)", len(plan.rows_to_insert))
    for row in plan.rows_to_insert:
        logger.info(
            "inserted driver_id=%s field=%s old_driver_id=%s old_vehicle_id=%s",
            row["driver_id"],
            row["field"],
            row["_old_driver_id"],
            row["_old_vehicle_id"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
