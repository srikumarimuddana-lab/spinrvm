#!/usr/bin/env python3
"""Purge SIN from confirmed-dormant, pre-launch-flagged driver profiles.

Thin CLI over ``services/dormant_driver_sin_purge_service.py``'s
``build_sin_purge_plan``/``apply_sin_purge``. See that module's docstring
for the full scope decision (SIN only, 180-day post-launch grace period,
population = drivers already flagged ``pre_launch_test = true`` by
``pre_launch_flag_service.py``) -- ACTION_ITEMS.md A34.

    # 1. See what would happen. Reads only -- no writes. (default)
    python backend/scripts/purge_dormant_driver_sin.py

    # 2. Actually purge -- only succeeds once the grace period has elapsed;
    #    refuses (exit 1) before the cutoff date the dry run above prints.
    python backend/scripts/purge_dormant_driver_sin.py --apply

Environment -- the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Safety, enforced in the service layer (not repeated here):
  - only touches drivers already flagged ``pre_launch_test = true`` --
    the same, single, admin-visible definition of "confirmed dormant" the
    Migration Checklist / admin drivers list already use. This script never
    recomputes or widens that population.
  - the grace-period gate is a hard code check in ``apply_sin_purge``, not
    just a documented convention -- passing ``--apply`` before the cutoff
    date refuses and exits non-zero rather than purging early.
  - deletes the actual vault.secrets ciphertext (migration 413's
    ``purge_driver_pii_secret`` RPC) before nulling the column reference --
    see migration 289's own warning that nulling the column alone orphans
    the secret without deleting it.
  - never logs, prints, or returns a raw SIN value -- only driver ids and
    counts.

PIPEDA: this is a data-minimization purge of a government ID with no
operational purpose (the target population has never taken a ride or gone
online), decided by the product owner 2026-09-11 -- not a DSAR/right-to-
delete request (that flow is ``purge_pii_retention()``, unrelated to this
script).

Rollback: **there is none once applied.** This deletes the vault ciphertext,
not just the column reference -- the SIN value itself is gone, by design
(that is the point of a purge, not a bug). The only way to recover a purged
driver's SIN is for that driver to re-submit it. Every purged driver's id is
printed and stamped into ``legacy_import_metadata.dormant_sin_purge``
(batch/purged_at/reason) so the population and timing remain auditable even
though the value itself does not.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("purge_dormant_driver_sin")


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
        from services import dormant_driver_sin_purge_service as svc
    except ImportError:  # pragma: no cover - CLI convenience
        from backend.services import dormant_driver_sin_purge_service as svc  # type: ignore

    plan = svc.build_sin_purge_plan()
    svc.print_report(plan, dry_run=not args.apply)

    if not args.apply:
        logger.info("dry run only — pass --apply to purge %d candidate(s)", len(plan.candidates))
        return 0

    if not plan.candidates:
        logger.info("nothing to apply")
        return 0

    if not plan.grace_period_elapsed:
        logger.error(
            "refusing to apply: grace period has not elapsed (eligible on %s)",
            plan.grace_period_cutoff,
        )
        return 1

    batch = args.batch or f"dormant-sin-purge-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    result = svc.apply_sin_purge(plan, batch=batch)
    logger.info("purged %d driver(s), batch=%s", len(result["purged"]), batch)
    for driver_id in result["purged"]:
        logger.info("purged sin for driver id=%s", driver_id)
    if result["conflicts"]:
        logger.warning(
            "%d candidate(s) skipped — sin changed between plan and apply "
            "(ids: %s); safe to re-run, these are re-evaluated automatically next time",
            len(result["conflicts"]),
            ", ".join(result["conflicts"]),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
