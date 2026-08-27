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
  - a CSV row whose phone/email already matches an EXISTING account is
    LINKED, never a competing duplicate — expect a real share of the export
    to hit this path (35.6% of the real export, confirmed against
    production), since many of these phones already resolved during the
    ride import (booking_import_service.py's own rider/driver phone
    matching). Two shapes, both additive-only: a match against an existing
    DRIVER enriches that driver's history (no new row); a match against an
    account with no driver yet gets a NEW driver row pointed at that
    existing user (no duplicate user, `is_driver` flipped True). Neither
    ever mutates a live driver's own name/phone/status/vehicle/rating.
  - every created driver lands status='needs_review', unverified, offline,
    unavailable — unconditionally. No document rows are created (the
    export's documents field is filenames only, no image bytes, nothing
    verifiable). No vehicle fields are set (vehicle_details.csv is a
    separate crosswalk — Phase 2, needs this driver row to exist first).

Rollback for an applied run — three different shapes printed separately,
because they touch different rows:
  - NEW rows (fresh user+driver, no existing-account match): delete those
    driver rows and their now-orphaned user rows, matched by the printed
    id/old_driver_id pairs — no cascading state (no payout, no ride, no
    Stripe call) is triggered by this write.
  - LINKED accounts (sub-population 1): delete the new driver row; on the
    existing user, remove this batch's entry from
    `legacy_import_metadata.mongo_driver_history` and only clear
    `is_driver` back to False if it was False before this run AND no other
    driver row now references that user — check both before touching it,
    since a real driver signup between apply and rollback would make
    clearing it wrong.
  - ENRICHED drivers (sub-population 2): remove this batch's entry from the
    existing driver's `legacy_import_metadata.mongo_driver_history` — no
    other field on that driver was touched, so nothing else to revert.
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

    total_planned = len(plan.drivers_to_insert) + len(plan.users_to_update) + len(plan.drivers_to_enrich)

    if not args.apply:
        logger.info(
            "dry run only — pass --apply to write %d new driver(s), link %d existing account(s), "
            "enrich %d existing driver(s)",
            len(plan.drivers_to_insert),
            len(plan.users_to_update),
            len(plan.drivers_to_enrich),
        )
        return 0

    if not total_planned:
        logger.info("nothing to apply")
        return 0

    svc.commit_mongo_driver_import_plan(plan)
    logger.info(
        "committed %d new driver(s), %d linked account(s), %d enriched driver(s), batch=%s",
        len(plan.drivers_to_insert),
        len(plan.users_to_update),
        len(plan.drivers_to_enrich),
        batch,
    )
    for driver in plan.drivers_to_insert:
        logger.info(
            "created driver id=%s old_driver_id=%s",
            driver["id"],
            driver["legacy_import_metadata"]["old_driver_id"],
        )
    for upd in plan.users_to_update:
        logger.info("linked new driver profile to existing user id=%s", upd["id"])
    for enrich in plan.drivers_to_enrich:
        logger.info("enriched existing driver id=%s history only", enrich["id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
