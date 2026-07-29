#!/usr/bin/env python3
"""Import completed bookings from the previous app into rides (CLI wrapper).

The parsing/validation/commit logic lives in
``backend/services/booking_import_service.py``. This script is a thin CLI on
top of it and re-exports the service's public symbols so callers/tests that
load this module by path keep working.

Dry-run first:
  python3 scripts/import_legacy_bookings.py \
    --bookings-csv legacy/bookings.csv \
    --customers-csv legacy/customers.csv \
    --drivers-csv legacy/drivers.csv \
    --earnings-csv legacy/driverearnings.csv \
    --service-area-name Saskatoon \
    --vehicle-type-name Economy \
    --dry-run

Commit after the dry-run report is clean:
  python3 scripts/import_legacy_bookings.py ... --commit

The report prints legacy booking codes and counts, never rider/driver PII.
Keep the CSVs in a secure location: they contain PIPEDA-covered personal
information (names, phones, exact addresses).

Only completed bookings whose customer AND driver are Canadian accounts are
imported; the previous vendor's test accounts are filtered out. Riders and
drivers are matched by phone number, and a booking whose party is not in this
app imports with a NULL link so it can be re-linked by a later re-run.

Because driver payouts were already settled in the previous app, each matched
driver also gets one offsetting ``payouts`` row so the imported earnings never
become withdrawable. Re-running is safe (already-imported bookings are skipped
and payout IDs are deterministic) — but pass the SAME ``--batch`` when resuming
a partial run, otherwise a second set of offset payouts is created under a new
batch ID.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))


def _load_import_core():
    """Load the shared import core as a top-level module.

    Loaded by file path rather than ``from services.booking_import_service
    import ...`` because ``backend/services/__init__.py`` eagerly imports the
    dispatch/fare services, which pull in FastAPI and the DB layer — the CLI
    only needs the dependency-light importer core. Loaded top-level, the
    module's dual-import falls back to ``from supabase_client import ...``.
    """
    if "booking_import_service" in sys.modules:
        return sys.modules["booking_import_service"]
    path = REPO_ROOT / "backend" / "services" / "booking_import_service.py"
    spec = importlib.util.spec_from_file_location("booking_import_service", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["booking_import_service"] = module
    spec.loader.exec_module(module)
    return module


_core = _load_import_core()
IMPORT_SOURCE = _core.IMPORT_SOURCE
PAYOUT_TYPE = _core.PAYOUT_TYPE
BookingImportPlan = _core.BookingImportPlan
ImportReportItem = _core.ImportReportItem
build_plan = _core.build_plan
commit_plan = _core.commit_plan
get_service_area = _core.get_service_area
get_vehicle_type = _core.get_vehicle_type
normalize_phone = _core.normalize_phone
parse_epoch_ms = _core.parse_epoch_ms
parse_money = _core.parse_money
payout_id_for = _core.payout_id_for
print_report = _core.print_report
read_csv = _core.read_csv
recount_drivers = _core.recount_drivers
supabase = _core.supabase


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import completed bookings from the previous app into rides"
    )
    parser.add_argument("--bookings-csv", type=Path, required=True)
    parser.add_argument("--customers-csv", type=Path, required=True)
    parser.add_argument("--drivers-csv", type=Path, required=True)
    parser.add_argument("--earnings-csv", type=Path, required=True)
    parser.add_argument("--service-area-id")
    parser.add_argument("--service-area-name", default="Saskatoon")
    parser.add_argument("--vehicle-type-id")
    parser.add_argument("--vehicle-type-name", default="Economy")
    parser.add_argument(
        "--batch",
        default=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        help="Import batch ID. Pass the SAME value when resuming a partial run.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--commit", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if supabase is None:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured"
        )

    bookings = read_csv(args.bookings_csv)
    customers = read_csv(args.customers_csv)
    drivers = read_csv(args.drivers_csv)
    earnings = read_csv(args.earnings_csv)

    service_area = get_service_area(args.service_area_id, args.service_area_name)
    vehicle_type = get_vehicle_type(args.vehicle_type_id, args.vehicle_type_name)

    print(f"Batch          : {args.batch}")
    print(f"Service area   : {service_area.get('name')} ({service_area.get('id')})")
    print(f"Vehicle type   : {vehicle_type.get('name')} ({vehicle_type.get('id')})")

    plan = build_plan(
        bookings,
        customers,
        drivers,
        earnings,
        service_area=service_area,
        vehicle_type=vehicle_type,
        batch=args.batch,
    )
    print_report(plan, dry_run=args.dry_run)

    if plan.errors:
        return 2
    if args.commit:
        commit_plan(plan)
        print(
            f"Committed {len(plan.rides_to_insert)} ride(s) and {len(plan.payouts_to_insert)} offset payout(s)."
        )
        print(
            f"Rollback: DELETE FROM payouts WHERE id LIKE 'legacy-import-{args.batch}-%'; "
            f"DELETE FROM rides WHERE legacy_import_metadata->>'batch' = '{args.batch}';"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
