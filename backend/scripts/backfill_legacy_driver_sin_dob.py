#!/usr/bin/env python3
"""Backfill SIN + date-of-birth for legacy-imported drivers from banks.csv.

Thin CLI over ``services/driver_import_service.py``'s
``plan_legacy_sin_dob_import``/``apply_legacy_sin_dob_import``. Source is two
files from the raw MongoDB export of the old app (``Mongo.zip``):
``banks.csv`` (SIN, DOB, keyed by a Mongo ObjectId ``driver_id``) and
``drivers.csv`` (the same export's driver collection, used only to resolve
that ObjectId to a phone number). See
docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md for how
this crosswalk was verified (100% joinable) and why raw bank account/transit/
institute numbers are deliberately NOT part of this script's scope — nothing
in the live payout path reads them (Stripe Connect collects banking directly
from the driver; the local ``bank_accounts`` table already discards the full
account number and its only payout consumer is permanently disabled).

    # 1. See what would change. Reads only — no writes. (default)
    python backend/scripts/backfill_legacy_driver_sin_dob.py \\
        --banks-csv Mongo/banks.csv --drivers-csv Mongo/drivers.csv

    # 2. Write SIN/DOB onto the matched drivers.
    python backend/scripts/backfill_legacy_driver_sin_dob.py \\
        --banks-csv Mongo/banks.csv --drivers-csv Mongo/drivers.csv --apply

Environment — the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Safety, enforced in the service layer (not repeated here):
  - only touches drivers already tagged with this repo's own
    ``legacy_saskatoon_driver_import`` source in ``legacy_import_metadata``;
    a phone coincidence can never touch an organic driver's SIN/DOB.
  - never clobbers an existing ``sin`` or ``date_of_birth`` — whatever is on
    file (self-entered, or from an earlier run of this script) wins.
  - SIN is Luhn-validated (``utils/sin.py``) before it is ever written, and
    is vault-encrypted via the same ``encrypt_driver_pii`` RPC
    ``license_number`` already uses (migration 289) — this script never
    holds a plaintext SIN in memory longer than one row's processing, and
    never logs one. DOB is stored plain (migration 221), matching how the
    original Saskatoon driver import already handles it.

PIPEDA: this reads plaintext SIN/DOB from a local CSV and writes SIN
encrypted-at-rest via the vault. Neither this script nor the service it
calls ever logs, prints, or returns a raw SIN or DOB value — only
``old_driver_id``/field-name/generic-message per report line.

Rollback for an applied run: every updated driver's ``id`` is printed. To
revert, null ``sin``/``sin_last4``/``sin_collected_at``/``date_of_birth`` for
those ids and remove the ``legacy_mongo_banks_sin_dob_import`` key from
``legacy_import_metadata`` — there is no cascading state (no payout, no
Stripe call) triggered by this write, so nothing else needs to be undone.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backfill_legacy_driver_sin_dob")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--banks-csv", required=True, type=Path, help="path to the export's banks.csv")
    parser.add_argument("--drivers-csv", required=True, type=Path, help="path to the export's drivers.csv")
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

    bank_rows = svc.read_csv(args.banks_csv)
    driver_rows = svc.read_csv(args.drivers_csv)

    plan = svc.plan_legacy_sin_dob_import(bank_rows, driver_rows)
    svc.print_sin_dob_report(plan, dry_run=not args.apply)

    if plan.errors:
        logger.error("refusing to apply: %d validation error(s) above", len(plan.errors))
        return 1

    if not args.apply:
        logger.info("dry run only — pass --apply to write %d update(s)", len(plan.updates))
        return 0

    if not plan.updates:
        logger.info("nothing to apply")
        return 0

    from datetime import datetime, timezone

    batch = args.batch or f"legacy-sin-dob-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    svc.apply_legacy_sin_dob_import(plan, batch=batch)
    logger.info("applied %d update(s), batch=%s", len(plan.updates), batch)
    for upd in plan.updates:
        logger.info("updated driver id=%s old_driver_id=%s", upd["id"], upd["old_driver_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
