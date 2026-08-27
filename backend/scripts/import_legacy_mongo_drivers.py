#!/usr/bin/env python3
"""Import legacy Mongo driver profiles from drivers.csv (raw MongoDB export).

Thin CLI over ``services/driver_import_service.py``'s
``build_mongo_driver_import_plan``/``commit_mongo_driver_import_plan``. See
docs/migration/2026-08-27-legacy-data-full-migration-approach.md Phase 1 and
the section-header comment above ``build_mongo_driver_import_plan`` in
``driver_import_service.py`` for the full design/safety reasoning.

    # 1. See what would be created. Reads only — no writes. (default)
    python backend/scripts/import_legacy_mongo_drivers.py \\
        --drivers-csv Mongo/drivers.csv

    # 2. Create the driver/user rows.
    python backend/scripts/import_legacy_mongo_drivers.py \\
        --drivers-csv Mongo/drivers.csv --apply

Environment — the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Safety, enforced in the service layer (not repeated here):
  - a CSV row whose phone/email already matches an EXISTING user or driver
    not created by a previous run of THIS importer is an error, never a
    silent merge — expect a real share of the export to hit this path, since
    many of these phones already resolved during the ride import
    (booking_import_service.py's own rider/driver phone matching).
  - every created driver lands status='needs_review', unverified, offline,
    unavailable — unconditionally. No document rows are created (the
    export's documents field is filenames only, no image bytes, nothing
    verifiable). No vehicle fields are set (vehicle_details.csv is a
    separate crosswalk — Phase 2, needs this driver row to exist first).

Rollback for an applied run: every created driver's id and old_driver_id are
printed. To revert, delete those driver rows (and their now-orphaned user
rows, matched by the same batch's phone list) — no cascading state (no
payout, no ride, no Stripe call) is triggered by this write.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("import_legacy_mongo_drivers")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drivers-csv", required=True, type=Path, help="path to the export's drivers.csv")
    parser.add_argument("--service-area-id", default=None)
    parser.add_argument("--service-area-name", default="Saskatoon")
    parser.add_argument("--apply", action="store_true", help="write to the DB (default: dry run)")
    parser.add_argument(
        "--batch",
        default=None,
        help="batch id stamped into legacy_import_metadata (default: timestamp-derived)",
    )
    args = parser.parse_args()

    try:
        from services import driver_import_service as svc
    except ImportError:  # pragma: no cover - CLI convenience
        from backend.services import driver_import_service as svc  # type: ignore

    # read_mongo_export_csv, NOT read_csv: drivers.csv is the raw Mongo
    # export, and read_csv's header normalization (built for the bespoke
    # Saskatoon driver CSV) silently mangles "_id" -> "id", breaking every
    # dedup/resume check keyed on it. See that function's own docstring.
    driver_rows = svc.read_mongo_export_csv(args.drivers_csv)

    service_area = svc.get_service_area(args.service_area_id, args.service_area_name)
    batch = args.batch or f"legacy-mongo-driver-import-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    plan = svc.build_mongo_driver_import_plan(driver_rows, service_area=service_area, import_batch=batch)
    svc.print_mongo_driver_import_report(plan, dry_run=not args.apply)

    if plan.errors:
        logger.error("refusing to apply: %d validation error(s) above", len(plan.errors))
        return 1

    if not args.apply:
        logger.info(
            "dry run only — pass --apply to write %d driver(s)/user(s)",
            len(plan.drivers_to_insert),
        )
        return 0

    if not plan.drivers_to_insert:
        logger.info("nothing to apply")
        return 0

    svc.commit_mongo_driver_import_plan(plan)
    logger.info("committed %d driver(s), batch=%s", len(plan.drivers_to_insert), batch)
    for driver in plan.drivers_to_insert:
        logger.info(
            "created driver id=%s old_driver_id=%s",
            driver["id"],
            driver["legacy_import_metadata"]["old_driver_id"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
