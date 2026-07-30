#!/usr/bin/env python3
"""Release card holds left open on rides that were cancelled and never billed.

This is the operator-run counterpart to the ``orphaned_hold_reconciler`` background
loop. The loop drains the same backlog on a 15-minute cadence once deployed; this
script exists so the backlog can be **sized and cleared immediately**, before a
deploy, since every affected rider currently has real money reserved on their card.

    # 1. See what is out there. Reads only — no Stripe calls, no writes.
    python backend/scripts/reconcile_orphaned_holds.py

    # 2. Release them.
    python backend/scripts/reconcile_orphaned_holds.py --apply

    # Clear a large backlog in bounded batches (default 50 per pass).
    python backend/scripts/reconcile_orphaned_holds.py --apply --all

Dry run is the default, deliberately: the non-default path cancels real Stripe
authorizations.

Environment — the same variables the backend itself reads, so a normal backend
``.env`` is sufficient:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Stripe keys are NOT read from the environment. They live in the ``app_settings``
Supabase table (see CLAUDE.md, "Settings in DB"), which ``cancel_authorization``
resolves per ride, so pointing this at a database is enough to point it at the
matching Stripe account. That also means **pointing it at production means moving
production money** — there is no separate Stripe switch to forget.

Safety properties inherited from the reconciler module:
  * only ever considers ``status='cancelled'`` rides — never a completed ride whose
    hold is owed to the driver;
  * only ``auth_status IN ('authorized','fare_only')`` — never a captured intent;
  * ``updated_at`` compare-and-swap per ride, so running this *while* the background
    loop is also running is safe;
  * Stripe idempotency key per (ride, intent), so a re-run is safe;
  * a failure on one ride never stops the rest.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Import as the backend package does, so the dual-import modules resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("reconcile_orphaned_holds")


async def _main(apply_changes: bool, drain_all: bool, batch: int) -> int:
    from utils.orphaned_hold_reconciler import reconcile_tick

    total = {"found": 0, "released": 0, "failed": 0, "not_claimed": 0, "skipped": 0}
    passes = 0

    while True:
        summary = await reconcile_tick(dry_run=not apply_changes, limit=batch)
        passes += 1

        if summary.get("error"):
            logger.error("candidate query failed: %s", summary["error"])
            return 2

        for k in total:
            total[k] += summary.get(k, 0)

        found = summary.get("found", 0)
        logger.info("pass %d: %s", passes, summary)

        if not drain_all or not apply_changes:
            break
        # Stop when a pass finds nothing, or when nothing could be acted on (which
        # would otherwise spin: the same rows keep matching the query).
        if found == 0 or (summary.get("released", 0) == 0 and summary.get("failed", 0) == 0):
            break
        if passes >= 200:  # backstop against an unexpected non-converging loop
            logger.warning("stopping after %d passes — re-run to continue", passes)
            break

    print()
    if apply_changes:
        print(f"  rides found         : {total['found']}")
        print(f"  holds released      : {total['released']}")
        print(f"  failed              : {total['failed']}   (money may still be held — investigate)")
        print(f"  lost claim race     : {total['not_claimed']}  (another replica handled them)")
    else:
        print(f"  rides found with an open hold : {total['found']}")
        print("  DRY RUN — nothing was cancelled or written.")
        print("  Re-run with --apply to release these holds.")
    print()

    # Non-zero when money may still be held, so a CI/cron invocation notices.
    return 1 if total["failed"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually cancel the Stripe authorizations (default is a read-only dry run)",
    )
    ap.add_argument(
        "--all",
        dest="drain_all",
        action="store_true",
        help="keep going in batches until the backlog is drained (implies --apply)",
    )
    ap.add_argument("--batch", type=int, default=50, help="rides per pass (default 50)")
    args = ap.parse_args()

    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        print(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.\n"
            "This script reads and writes the rides table and cancels Stripe\n"
            "authorizations for whichever project those point at.",
            file=sys.stderr,
        )
        return 2

    if args.drain_all and not args.apply:
        print("--all implies --apply; pass both explicitly to confirm.", file=sys.stderr)
        return 2

    return asyncio.run(_main(args.apply, args.drain_all, args.batch))


if __name__ == "__main__":
    raise SystemExit(main())
