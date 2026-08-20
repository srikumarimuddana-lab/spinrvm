#!/usr/bin/env python3
"""Verify migration 332's Period-2/Period-3 reconstruction for the 186
legacy-imported rides against driverlocationlogs.csv's real phase-boundary
timestamps. Closes the Oct 30 checklist's item #5(a) -- see
docs/runbooks/legacy-migration-playbook.md and ACTION_ITEMS.md A41.

Thin CLI over backend/services/insurance_period_reconstruction_verification.py's
fetch_migration_332_candidate_rides / stream_driverlocationlogs_phase_spans /
build_verification_plan pipeline.

    # See what the real CSV data says about the 186 rides. Read-only --
    # queries Supabase (SELECT only) and streams the CSV; no DB writes.
    python backend/scripts/verify_legacy_insurance_period_reconstruction.py \\
        --driverlocationlogs-csv Mongo/driverlocationlogs.csv

Environment -- the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

--apply is intentionally accepted (for CLI-shape symmetry with the other
legacy backfill scripts) but always refused: this pass has no DB write
path. See the service module's docstring for why -- in short,
driver_insurance_periods is append-only/immutable and there is no
driver_insurance_period_corrections table (yet) to route a genuine
correction into, so writing a second, competing set of period rows for an
already-covered ride would create an unresolvable contradiction for anyone
reading the table later. Do not add a write path to this script without
first building that corrections table and updating every consumer
(scripts/compliance_export.py, backend/routes/admin/driver_distance.py) to
know how to prefer one source over another.

PIPEDA: this script and the service it calls never surface a raw GPS
coordinate (latitude/longitude) anywhere -- not to stdout, a log line, or a
return value. driverlocationlogs.csv's way_points column is never read by
the streaming CSV reader beyond the row it's part of.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("verify_legacy_insurance_period_reconstruction")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--driverlocationlogs-csv", required=True, type=Path, help="path to the export's driverlocationlogs.csv"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="refused unconditionally -- see this file's module docstring for why",
    )
    args = parser.parse_args()

    try:
        from services import insurance_period_reconstruction_verification as svc
        from supabase_client import supabase
    except ImportError:  # pragma: no cover - CLI convenience
        from backend.services import insurance_period_reconstruction_verification as svc  # type: ignore
        from backend.supabase_client import supabase  # type: ignore

    if args.apply:
        logger.error("--apply is refused: this verification pass has no DB write path (see module docstring)")
        return 1

    candidates = svc.fetch_migration_332_candidate_rides(supabase)
    logger.info("fetched %d candidate ride(s) from migration 332's scope", len(candidates))

    booking_ids = {c.old_booking_id for c in candidates if c.old_booking_id}
    spans_by_booking = svc.stream_driverlocationlogs_phase_spans(args.driverlocationlogs_csv, booking_ids)
    logger.info("matched phase spans for %d of %d booking id(s)", len(spans_by_booking), len(booking_ids))

    plan = svc.build_verification_plan(candidates, spans_by_booking)
    svc.print_verification_report(plan, dry_run=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
