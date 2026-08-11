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
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backfill_statement_totals")

# PostgREST caps an unbounded select at db-max-rows without signalling it.
_PAGE_SIZE = 500


def _parse_date(value) -> date | None:
    """Accept a date, a 'YYYY-MM-DD' string, or a full ISO timestamp."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _money_keys(totals: dict) -> dict:
    """The three figures the job stores and the admin list renders."""
    return {
        "earnings": totals.get("earnings"),
        "payouts_total": totals.get("payouts_total"),
        "trips": totals.get("trips"),
    }


def _corrected(old_totals: dict, statement: dict) -> dict:
    """New totals, preserving the pre-backfill figures for rollback/audit.

    ``superseded`` is written once: on a re-run the row is skipped as
    unchanged, and even if it were not, an existing ``superseded`` is never
    overwritten — the ORIGINAL job-time figures are what an auditor needs,
    not the previous run's.
    """
    corrected = {
        "earnings": statement["earnings"],
        "payouts_total": statement["payouts_total"],
        "trips": statement["trips"],
    }
    superseded = (old_totals or {}).get("superseded")
    if superseded is None:
        superseded = _money_keys(old_totals or {})
        superseded["reason"] = "dropped_upper_bound_filter_bug"
    corrected["superseded"] = superseded
    return corrected


async def _load_statements(db, driver_id: str | None, since: str | None, limit: int | None) -> list[dict]:
    filters: dict = {}
    if driver_id:
        filters["driver_id"] = driver_id
    if since:
        filters["period_start"] = {"$gte": since}

    rows: list[dict] = []
    offset = 0
    while True:
        page = (
            await db.get_rows(
                "driver_statements",
                filters,
                order="period_start",
                desc=True,
                limit=_PAGE_SIZE,
                offset=offset,
            )
            or []
        )
        rows.extend(page)
        if len(page) < _PAGE_SIZE or (limit and len(rows) >= limit):
            break
        offset += _PAGE_SIZE
    return rows[:limit] if limit else rows


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the corrected totals (default: dry run)")
    parser.add_argument("--driver-id", help="restrict to one driver")
    parser.add_argument("--since", help="only statements with period_start >= this date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, help="cap the number of statements considered")
    args = parser.parse_args()

    try:
        import db_supabase as db
        from utils.driver_statement import build_custom_statement, build_statement
    except ImportError:  # pragma: no cover - CLI convenience
        from backend import db_supabase as db  # type: ignore
        from backend.utils.driver_statement import build_custom_statement, build_statement  # type: ignore

    statements = await _load_statements(db, args.driver_id, args.since, args.limit)
    logger.info("considering %d statement(s)%s", len(statements), "" if args.apply else " (DRY RUN — no writes)")

    drivers: dict[str, dict] = {}
    changed = 0
    unchanged = 0
    skipped: list[str] = []
    failed: list[str] = []
    # Net movement across every corrected row, so the operator can sanity-check
    # the direction of the correction before applying it.
    delta_payouts = 0.0
    delta_earnings = 0.0

    for row in statements:
        sid = str(row.get("id"))
        driver_id = row.get("driver_id")
        period_type = (row.get("period_type") or "").strip()
        start_d = _parse_date(row.get("period_start"))
        end_d = _parse_date(row.get("period_end"))

        if not driver_id or start_d is None:
            skipped.append(f"{sid}: missing driver_id or unparseable period_start")
            continue

        driver = drivers.get(driver_id)
        if driver is None:
            found = await db.get_rows("drivers", {"id": driver_id}, limit=1)
            if not found:
                skipped.append(f"{sid}: driver {driver_id} no longer exists")
                continue
            driver = found[0]
            drivers[driver_id] = driver

        try:
            if period_type in ("weekly", "monthly"):
                statement = await build_statement(driver, period_type, start_d)
            elif end_d is not None:
                # Custom ranges (admin date filter) have no anchor to derive
                # bounds from — rebuild over the stored inclusive range.
                statement = await build_custom_statement(driver, start_d, end_d)
            else:
                skipped.append(f"{sid}: period_type {period_type!r} has no period_end to rebuild from")
                continue
        except Exception as e:
            logger.error("[%s] rebuild failed: %s", sid, e, exc_info=True)
            failed.append(sid)
            continue

        old = row.get("totals") or {}
        new = _corrected(old, statement)

        if _money_keys(old) == _money_keys(new):
            unchanged += 1
            continue

        changed += 1
        try:
            delta_payouts += float(new["payouts_total"]) - float(old.get("payouts_total") or 0)
            delta_earnings += float(new["earnings"]) - float(old.get("earnings") or 0)
        except (TypeError, ValueError):
            pass

        logger.info(
            "[%s] %s %s: earnings %s -> %s | paid out %s -> %s | trips %s -> %s",
            sid,
            period_type,
            start_d,
            old.get("earnings"),
            new["earnings"],
            old.get("payouts_total"),
            new["payouts_total"],
            old.get("trips"),
            new["trips"],
        )

        if args.apply:
            try:
                # update_one returns None when it matched ZERO rows — a silent
                # no-op that would otherwise be counted as a correction. Treat
                # it as a failure: a money backfill reporting writes it never
                # made is worse than one that reports an error.
                written = await db.update_one("driver_statements", {"id": sid}, {"totals": new})
                if not written:
                    logger.error("[%s] update matched no rows — not corrected", sid)
                    failed.append(sid)
            except Exception as e:
                logger.error("[%s] write failed: %s", sid, e, exc_info=True)
                failed.append(sid)

    logger.info(
        "%s: %d corrected, %d already correct, %d skipped, %d failed",
        "APPLIED" if args.apply else "DRY RUN",
        changed,
        unchanged,
        len(skipped),
        len(failed),
    )
    if changed:
        logger.info("net movement across corrected rows: earnings %+.2f | paid out %+.2f", delta_earnings, delta_payouts)
    for note in skipped:
        logger.warning("skipped %s", note)
    if failed:
        logger.error("failed statement ids: %s", json.dumps(failed))
        return 1
    if not args.apply and changed:
        logger.info("re-run with --apply to write these %d correction(s)", changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
