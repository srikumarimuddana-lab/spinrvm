#!/usr/bin/env python3
"""Backfill ``legacy_import_metadata.duration_estimated`` onto already-imported
legacy rides (the 2026-07-29 booking import, and any earlier legacy batch).

Thin CLI over ``services/booking_import_service.py``'s
``plan_duration_estimated_backfill``/``apply_duration_estimated_backfill``.
Follow-up to
docs/change-log/2026-08-19-legacy-migration-transparency-backend.md (§3):
that fix stamps ``duration_estimated`` on every ride the importer commits
*going forward* (e.g. the Oct 30 final cutover), but explicitly did not touch
rides already committed by earlier imports — writing to already-live ride
rows was called out there as a separate, larger decision requiring its own
sign-off. This script is that follow-up, built but never applied this
session (see the Change Impact & Risk Log entry for this backfill).

Unlike ``backfill_legacy_driver_sin_dob.py``, this script needs no external
CSV: everything it needs (whether ``ride_started_at`` is set) is already on
the committed ``rides`` row, so the plan step reads directly from Supabase.

    # 1. See what would change. Reads only — no writes. (default)
    python backend/scripts/backfill_legacy_ride_duration_estimated.py

    # 2. Stamp duration_estimated onto the matched rides.
    python backend/scripts/backfill_legacy_ride_duration_estimated.py --apply

Environment — the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Safety, enforced in the service layer (not repeated here):
  - only scans rides already tagged with this repo's own
    ``legacy_mongo_booking_import`` source in ``legacy_import_metadata``.
  - never clobbers a row that already has a ``duration_estimated`` key (from
    the importer itself, or an earlier run of this script) — whatever is
    already stamped wins.
  - never touches ``duration_minutes`` — only ``legacy_import_metadata``.
  - a write-time guard (re-read + a PostgREST ``IS NULL`` filter on the
    update itself) prevents two concurrent runs of this script from
    double-stamping or clobbering the same row.

PIPEDA: reports and logs carry only ride ids (internal UUIDs) and counts —
never addresses, names, or any other ride PII.

Rollback for an applied run: every updated ride's ``id`` is printed. To
revert, for each id remove the ``duration_estimated`` and
``legacy_duration_estimated_backfill`` keys from ``legacy_import_metadata``
(leaving every other key untouched) — there is no cascading state (no fare
recompute, no payout, no Stripe call) triggered by this write, so nothing
else needs to be undone. ``duration_minutes`` itself is never written by
this script, so there is nothing to revert there.

**Operational note (not this script's decision):** who runs this with
``--apply``, against which environment, and when, is a follow-up rollout
decision this script deliberately does not make — see the Change Impact &
Risk Log entry for what still needs sign-off before that happens.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backfill_legacy_ride_duration_estimated")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write to the DB (default: dry run)")
    parser.add_argument(
        "--batch",
        default=None,
        help="batch id stamped into legacy_import_metadata (default: timestamp-derived)",
    )
    args = parser.parse_args()

    try:
        from services import booking_import_service as svc
    except ImportError:  # pragma: no cover - CLI convenience
        from backend.services import booking_import_service as svc  # type: ignore

    plan = svc.plan_duration_estimated_backfill()
    svc.print_duration_estimated_backfill_report(plan, dry_run=not args.apply)

    if plan.errors:
        logger.error("refusing to apply: %d validation error(s) above", len(plan.errors))
        return 1

    if not args.apply:
        logger.info("dry run only — pass --apply to write %d update(s)", len(plan.updates))
        return 0

    if not plan.updates:
        logger.info("nothing to apply")
        return 0

    batch = args.batch or f"legacy-duration-estimated-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    conflicts = svc.apply_duration_estimated_backfill(plan, batch=batch)
    applied = len(plan.updates) - len(conflicts)
    logger.info("applied %d update(s), batch=%s", applied, batch)
    for upd in plan.updates:
        if upd.id not in conflicts:
            logger.info("updated ride id=%s old_booking_id=%s", upd.id, upd.old_booking_id)
    if conflicts:
        logger.warning(
            "%d update(s) skipped — already marked between plan and apply (ride id: %s); "
            "safe to re-run, these are excluded automatically next time",
            len(conflicts),
            ", ".join(conflicts),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
