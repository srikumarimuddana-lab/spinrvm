#!/usr/bin/env python3
"""Correct migration 332's Period-2 reconstruction for the legacy-imported
rides where `driverlocationlogs.csv`'s real phase data diverges from it --
by writing sanctioned `driver_insurance_period_corrections` rows (migration
355), never by mutating `driver_insurance_periods` itself (impossible
anyway -- it's append-only/immutable, see that table's own trigger).

Sign-off / decision this implements: docs/migration/2026-08-27-legacy-data-
full-migration-approach.md §5b. Builds on
backend/services/insurance_period_reconstruction_verification.py's
already-shipped classification (reused unchanged) and
backend/services/insurance_period_gps_correction.py's new write path.

    # Report only -- no DB writes. Always run this first.
    python backend/scripts/apply_legacy_insurance_period_gps_corrections.py \\
        --driverlocationlogs-csv Mongo/driverlocationlogs.csv \\
        --operator-user-id <your users.id>

    # For real, once the report above looks right.
    python backend/scripts/apply_legacy_insurance_period_gps_corrections.py \\
        --driverlocationlogs-csv Mongo/driverlocationlogs.csv \\
        --operator-user-id <your users.id> \\
        --apply

Environment -- the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

--operator-user-id is required and is not a formality: `driver_insurance_
period_corrections.corrected_by` is a NOT NULL FK to `users(id)` -- this
regulatory audit table records who authorized each correction, same as any
other compliance-facing action in this codebase. Pass a real admin's
`users.id`, not a placeholder.

Only DIVERGES rides (per the verification classification) get a
correction; CONFIRMED rides need nothing, and every other status (no CSV
data, ambiguous spans, unknown phase, ...) is left as the existing
is_reconstructed=true estimate -- disclosed, not silently improved on a
guess. Re-running is safe: already-corrected rows are detected and
skipped (both proactively, and by migration 355's own UNIQUE index as the
final backstop).

Read side needs no change -- scripts/compliance_export.py and backend/
routes/admin/driver_distance.py already prefer a correction over the
original span when one exists, and already flag it via is_corrected. This
script is the only missing piece.

PIPEDA: never surfaces a raw GPS coordinate anywhere -- inherited from the
verification module's PhaseSpan/RideVerification types, neither of which
ever carries way_points.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("apply_legacy_insurance_period_gps_corrections")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--driverlocationlogs-csv", required=True, type=Path, help="path to the export's driverlocationlogs.csv"
    )
    parser.add_argument(
        "--operator-user-id",
        required=True,
        help="a real admin's users.id -- recorded as driver_insurance_period_corrections.corrected_by",
    )
    parser.add_argument(
        "--reason",
        default=None,
        help="override the default correction reason (must be non-blank if provided)",
    )
    parser.add_argument(
        "--tolerance-seconds",
        type=float,
        default=None,
        help="override the verification pass's divergence tolerance (default: the service module's own default)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write the correction rows. Without this flag, report only -- no DB writes.",
    )
    args = parser.parse_args()

    try:
        from services import insurance_period_gps_correction as corr
        from services import insurance_period_reconstruction_verification as verify
        from supabase_client import supabase
    except ImportError:  # pragma: no cover - CLI convenience
        from backend.services import insurance_period_gps_correction as corr  # type: ignore
        from backend.services import insurance_period_reconstruction_verification as verify  # type: ignore
        from backend.supabase_client import supabase  # type: ignore

    candidates = verify.fetch_migration_332_candidate_rides(supabase)
    logger.info("fetched %d candidate ride(s) from migration 332's scope", len(candidates))

    booking_ids = {c.old_booking_id for c in candidates if c.old_booking_id}
    spans_by_booking = verify.stream_driverlocationlogs_phase_spans(args.driverlocationlogs_csv, booking_ids)
    logger.info("matched phase spans for %d of %d booking id(s)", len(spans_by_booking), len(booking_ids))

    kwargs = {}
    if args.tolerance_seconds is not None:
        kwargs["tolerance_seconds"] = args.tolerance_seconds
    verification_plan = verify.build_verification_plan(candidates, spans_by_booking, **kwargs)
    verify.print_verification_report(verification_plan, dry_run=not args.apply)
    print()

    diverging_ride_ids = [r.ride_id for r in verification_plan.results if r.status == corr.CORRECTABLE_STATUS]
    period_2_rows = corr.fetch_period_2_rows_for_rides(supabase, diverging_ride_ids)
    already_corrected = corr.fetch_existing_correction_period_ids(
        supabase, [row["id"] for row in period_2_rows.values()]
    )
    logger.info(
        "%d DIVERGES ride(s), %d already have a correction on file",
        len(diverging_ride_ids),
        len(already_corrected & {row["id"] for row in period_2_rows.values()}),
    )

    reason_kwargs = {"reason": args.reason} if args.reason else {}
    plan = corr.build_correction_plan(
        verification_plan,
        period_2_rows,
        already_corrected,
        operator_user_id=args.operator_user_id,
        **reason_kwargs,
    )
    corr.print_correction_report(plan, dry_run=not args.apply)

    if not args.apply:
        print("\nDry run only -- pass --apply to actually write these correction rows.")
        return 0

    if not plan.to_insert:
        print("\nNothing to apply -- no DIVERGES rides need a new correction.")
        return 0

    result = corr.commit_correction_plan(supabase, plan)
    print(f"\nApplied: inserted {result['inserted']} correction row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
