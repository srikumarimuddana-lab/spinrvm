#!/usr/bin/env python3
"""Recompute the stored ``driver_statements.totals`` written under the
dropped-upper-bound filter bug.

``_apply_filters`` used an if/elif chain, so a two-sided range
(``{"$gte": start, "$lt": end}`` — how every statement scopes its period)
compiled to the LOWER BOUND ALONE. Each statement therefore summed rides,
bonuses and payouts from its period start **to the present** instead of to
its period end. Observed in production as six consecutive statements all
reporting the same ``$115.70 paid out``, with monthly earnings shrinking as
the start date advanced (116.82 -> 95.27 -> 47.75) — the signature of an
unbounded window, not of real activity.

The compiler is fixed, so everything computed live (PDF downloads, emailed
copies, the driver app, T4A) is already correct. But the admin statements
LIST renders ``driver_statements.totals``, a JSON column frozen at job time —
those rows keep the wrong numbers until they are rewritten. That is what this
script does.

    # 1. See what would change. Reads only — no writes. (default)
    python backend/scripts/backfill_statement_totals.py

    # 2. Write the corrected totals.
    python backend/scripts/backfill_statement_totals.py --apply

    # Narrow the scope while verifying:
    python backend/scripts/backfill_statement_totals.py --driver-id <uuid>
    python backend/scripts/backfill_statement_totals.py --since 2026-01-01
    python backend/scripts/backfill_statement_totals.py --limit 50 --apply

Environment — the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Safety properties:
  * Dry run is the DEFAULT. This rewrites stored money figures on a
    driver-facing audit surface; nothing is written without --apply.
  * ONLY the ``totals`` column is touched. ``status``, ``email_sent_at`` and
    ``failure_reason`` are historical facts about what was sent to the driver
    at the time — a statement that was emailed with wrong numbers really was
    emailed, and rewriting that record would erase the evidence. The old
    figures are preserved under ``totals.superseded`` (see _corrected) so the
    original is never destroyed.
  * Idempotent: recomputation is pure, and a row already matching its
    recomputed values is skipped, so re-running converges and changes nothing
    on the second pass.
  * A row whose driver no longer exists, or whose period columns are
    unparseable, is COUNTED AND REPORTED, never silently skipped — an
    unexplained gap in a money backfill is how a partial run gets mistaken
    for a complete one.
  * One row's failure never aborts the rest; failures are logged with the
    statement id and re-raised as a non-zero exit at the end.
  * Reads are paged. An unbounded PostgREST select silently caps at
    db-max-rows (1000), which would leave older statements uncorrected while
    reporting success.

Rollback: every rewritten row keeps its previous figures at
``totals.superseded``, so the original state is recoverable without a
restore:

    UPDATE driver_statements
       SET totals = totals - 'superseded' || (totals->'superseded')
     WHERE totals ? 'superseded';
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backfill_statement_totals")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the corrected totals (default: dry run)")
    parser.add_argument("--driver-id", action="append", dest="driver_ids", help="restrict to a driver (repeatable)")
    parser.add_argument("--since", help="only statements with period_start >= this date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None, help="cap the statements considered (default: all)")
    args = parser.parse_args()

    # The admin "Fix Statement Totals" button calls this same service, so the
    # CLI and the UI can never apply a different correction.
    try:
        from services.statement_totals_backfill import recompute_statement_totals
    except ImportError:  # pragma: no cover - CLI convenience
        from backend.services.statement_totals_backfill import recompute_statement_totals  # type: ignore

    result = await recompute_statement_totals(
        driver_ids=args.driver_ids,
        since=args.since,
        limit=args.limit,
        apply=args.apply,
    )

    for c in result.changes:
        logger.info(
            "[%s] %s %s: earnings %s -> %s | paid out %s -> %s | trips %s -> %s",
            c.statement_id, c.period_type, c.period_start,
            c.before["earnings"], c.after["earnings"],
            c.before["payouts_total"], c.after["payouts_total"],
            c.before["trips"], c.after["trips"],
        )

    logger.info(
        "%s: %d corrected, %d already correct, %d skipped, %d failed",
        "APPLIED" if result.applied else "DRY RUN",
        result.corrected, result.unchanged, len(result.skipped), len(result.failed),
    )
    if result.corrected:
        logger.info(
            "net movement across corrected rows: earnings %+.2f | paid out %+.2f",
            result.delta_earnings, result.delta_payouts,
        )
    for note in result.skipped:
        logger.warning("skipped %s", note)
    if result.has_more:
        logger.warning("more statements remain beyond --limit; re-run to continue")
    if result.failed:
        logger.error("failed statement ids: %s", json.dumps(result.failed))
        return 1
    if not result.applied and result.corrected:
        logger.info("re-run with --apply to write these %d correction(s)", result.corrected)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
