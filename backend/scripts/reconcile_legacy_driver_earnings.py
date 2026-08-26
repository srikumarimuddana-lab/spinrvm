#!/usr/bin/env python3
"""Reconcile imported legacy rides' driver_earnings against driverearnings.csv.

Read-only diagnostic — never writes anything. Per the 2026-08-19 Mongo-export
audit (docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md,
`driverearnings` finding): booking_import_service.py's `driver_total` for a
completed legacy ride is EITHER the sum of that booking's rows in
`driverearnings.csv` (the normal case), OR a fallback to the booking's own
`you_earn` field when no earnings row exists (4 rows in the reference
export). This collection "has never been cross-checked against the imported
payouts rows" — this script is that check, run against whatever rides are
already in production.

Usage (run from the backend server where SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY
are available):

    cd backend
    python scripts/reconcile_legacy_driver_earnings.py --earnings-csv /path/to/driverearnings.csv

Buckets every already-imported completed legacy ride into:
  - MATCH:     driverearnings.csv rows for this booking sum to (within
               --tolerance of) rides.driver_earnings.
  - FALLBACK:  no driverearnings.csv rows exist for this booking at all —
               expected for the ~4 rows booking_import_service.py's own
               you_earn fallback covers; reported separately, not as a bug.
  - MISMATCH:  driverearnings.csv rows exist but don't sum to what's stored
               on the ride, beyond --tolerance. This is the actual finding
               this script exists to surface.

Never outputs rider/driver names, phone numbers, or addresses — only the
legacy booking id (already non-PII per this importer's own report convention) and
dollar figures.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import db_supabase
except ImportError:
    from backend import db_supabase

try:
    from services.booking_import_service import IMPORT_SOURCE
except ImportError:
    from backend.services.booking_import_service import IMPORT_SOURCE

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ZERO = Decimal("0")
DEFAULT_TOLERANCE = Decimal("0.01")


def _parse_money(value: str) -> Decimal:
    """Same tolerance as booking_import_service.parse_money: blank -> 0,
    unparseable -> 0 (reported separately as a parse-error row, never
    silently treated as a real zero -- see _load_earnings_by_booking)."""
    raw = (value or "").strip()
    if not raw:
        return ZERO
    try:
        return Decimal(raw)
    except InvalidOperation:
        return ZERO


@dataclass
class ReconcileResult:
    old_booking_id: str
    ride_id: str
    stored_driver_earnings: Decimal
    ledger_sum: Decimal
    ledger_row_count: int
    bucket: str  # "match" | "fallback" | "mismatch"


def _load_earnings_by_booking(earnings_csv_path: str) -> tuple[dict[str, Decimal], dict[str, int], list[str]]:
    """Mirrors booking_import_service._earnings_by_booking's grouping exactly
    (same "ignore blank booking_id" rule, same field name) so this script
    compares against precisely what the importer itself would compute --
    not a re-derivation that could silently drift from the real logic.

    Returns (sum_by_booking, row_count_by_booking, unparseable_amount_rows).
    """
    sums: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    unparseable: list[str] = []
    with open(earnings_csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            booking_id = (row.get("booking_id") or "").strip()
            if not booking_id:
                continue  # referral-bonus rows, same exclusion as the importer
            raw_amount = (row.get("amount") or "").strip()
            if raw_amount:
                try:
                    Decimal(raw_amount)
                except InvalidOperation:
                    unparseable.append((row.get("_id") or booking_id))
            amount = _parse_money(raw_amount)
            sums[booking_id] = sums.get(booking_id, ZERO) + amount
            counts[booking_id] = counts.get(booking_id, 0) + 1
    return sums, counts, unparseable


async def reconcile(earnings_csv_path: str, *, tolerance: Decimal, limit: int | None) -> list[ReconcileResult]:
    ledger_sums, ledger_counts, unparseable_amount_rows = _load_earnings_by_booking(earnings_csv_path)
    if unparseable_amount_rows:
        logger.warning(
            "driverearnings.csv: %d row(s) had an unparseable 'amount' field, treated as $0 in the ledger sum "
            "(not silently dropped -- flagged here): %s",
            len(unparseable_amount_rows),
            ", ".join(unparseable_amount_rows[:20]),
        )

    rides = await db_supabase.get_rows(
        "rides",
        {"legacy_import_metadata": {"$notnull": True}},
        columns="id,driver_earnings,status,legacy_import_metadata",
        limit=limit or 5000,
    )

    results: list[ReconcileResult] = []
    skipped_other_source = 0
    skipped_no_booking_id = 0
    for r in rides:
        meta = r.get("legacy_import_metadata") or {}
        if meta.get("source") != IMPORT_SOURCE:
            # Not a booking_import_service.py row (e.g. driver/rider import's
            # own legacy_import_metadata shape) -- out of scope for this
            # reconciliation, never guessed at.
            skipped_other_source += 1
            continue
        old_booking_id = (meta.get("old_booking_id") or "").strip()
        if not old_booking_id:
            skipped_no_booking_id += 1
            continue
        if r.get("status") != "completed":
            # Cancelled/failed legacy rows never carry earnings (see
            # booking_import_service.py's module docstring) -- reconciling
            # them against driverearnings.csv would be comparing $0 against
            # $0 for no informational value.
            continue

        stored = Decimal(str(r.get("driver_earnings") if r.get("driver_earnings") is not None else "0"))
        ledger_sum = ledger_sums.get(old_booking_id, ZERO)
        row_count = ledger_counts.get(old_booking_id, 0)

        if row_count == 0:
            bucket = "fallback"
        elif abs(stored - ledger_sum) <= tolerance:
            bucket = "match"
        else:
            bucket = "mismatch"

        results.append(
            ReconcileResult(
                old_booking_id=old_booking_id,
                ride_id=r["id"],
                stored_driver_earnings=stored,
                ledger_sum=ledger_sum,
                ledger_row_count=row_count,
                bucket=bucket,
            )
        )

    if skipped_other_source:
        logger.info(
            "Skipped %d ride(s) imported by a different source (not booking_import_service.py)", skipped_other_source
        )
    if skipped_no_booking_id:
        logger.warning("Skipped %d ride(s) with legacy_import_metadata but no old_booking_id", skipped_no_booking_id)

    return results


def print_report(results: list[ReconcileResult]) -> None:
    matches = [r for r in results if r.bucket == "match"]
    fallbacks = [r for r in results if r.bucket == "fallback"]
    mismatches = [r for r in results if r.bucket == "mismatch"]

    print("\n=== Legacy driver_earnings reconciliation ===")
    print(f"  rides checked         : {len(results)}")
    print(f"  matches               : {len(matches)}")
    print(f"  fallback (no ledger)  : {len(fallbacks)}  (expected -- you_earn fallback path)")
    print(f"  MISMATCHES            : {len(mismatches)}")

    if fallbacks:
        print(f"\n  --- fallback rows (informational, {len(fallbacks)}) ---")
        for r in fallbacks[:20]:
            print(f"    booking={r.old_booking_id} ride={r.ride_id} stored=${r.stored_driver_earnings}")
        if len(fallbacks) > 20:
            print(f"    … and {len(fallbacks) - 20} more")

    if mismatches:
        print(f"\n  --- MISMATCHES ({len(mismatches)}) ---")
        for r in mismatches:
            delta = r.stored_driver_earnings - r.ledger_sum
            print(
                f"    booking={r.old_booking_id} ride={r.ride_id} "
                f"stored=${r.stored_driver_earnings} ledger=${r.ledger_sum} "
                f"({r.ledger_row_count} ledger row(s)) delta=${delta}"
            )
        print("\n  These need manual review -- this script does not write any correction.")
    print()


async def main(earnings_csv_path: str, *, tolerance: Decimal, limit: int | None) -> int:
    results = await reconcile(earnings_csv_path, tolerance=tolerance, limit=limit)
    print_report(results)
    mismatches = sum(1 for r in results if r.bucket == "mismatch")
    return 1 if mismatches else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--earnings-csv", required=True, help="Path to the legacy driverearnings.csv export")
    parser.add_argument(
        "--tolerance",
        type=str,
        default=str(DEFAULT_TOLERANCE),
        help=f"Dollar tolerance before flagging a mismatch (default {DEFAULT_TOLERANCE})",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Max imported rides to check (default: all, up to 5000)"
    )
    args = parser.parse_args()
    exit_code = asyncio.run(main(args.earnings_csv, tolerance=Decimal(args.tolerance), limit=args.limit))
    sys.exit(exit_code)
