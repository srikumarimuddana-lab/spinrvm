"""Recompute stored ``driver_statements.totals``.

Shared by the admin "Recompute statement totals" button
(``routes/admin/driver_statements.py``) and the CLI
(``scripts/backfill_statement_totals.py``) so the two can never drift — the
button and the script apply exactly the same correction with exactly the same
safety properties.

**Why this exists.** ``_apply_filters`` used an if/elif chain, so a two-sided
range (``{"$gte": start, "$lt": end}`` — how every statement scopes its
period) compiled to the LOWER BOUND ALONE. Each statement therefore summed
rides, bonuses and payouts from its period start *to the present* instead of
to its period end. Observed in production as six consecutive statements all
reporting ``$115.70 paid out``, with monthly earnings shrinking as the start
date advanced — the signature of an unbounded window, not of real activity.

The compiler is fixed, so everything computed live (PDF downloads, emailed
copies, the driver app, T4A) is already correct. The admin statements LIST
renders ``driver_statements.totals``, a JSON column frozen at job time, so
those rows keep the wrong figures until rewritten here.

Safety properties (identical on both entry points):

* **Dry run is the default.** ``apply=False`` computes the full diff and
  writes nothing.
* **Only ``totals`` is written.** ``status`` / ``email_sent_at`` /
  ``failure_reason`` are historical facts about what was actually sent — a
  statement emailed with wrong numbers really was emailed, and rewriting that
  would erase the evidence.
* **The original figures survive** at ``totals.superseded``, so the rewrite is
  reversible without a restore and an auditor can still see what the driver
  was shown. A re-run never overwrites that first snapshot.
* **Idempotent.** A row already matching its recomputed values is skipped, so
  a second pass changes nothing.
* **A zero-row update is a FAILURE**, not a silent success.
* **Reads are paged** past the PostgREST db-max-rows cap (1000), which would
  otherwise leave older statements uncorrected while reporting success.
* **Nothing is silently dropped.** Missing drivers and unparseable periods are
  reported as skips; one row's failure never aborts the rest.

Rollback for an applied run:

    UPDATE driver_statements
       SET totals = totals - 'superseded' || (totals->'superseded')
     WHERE totals ? 'superseded';
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

try:
    from .. import db_supabase
    from ..utils.driver_statement import build_custom_statement, build_statement
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    import db_supabase  # type: ignore
    from utils.driver_statement import build_custom_statement, build_statement  # type: ignore

logger = logging.getLogger(__name__)


class UnsafeBuildError(RuntimeError):
    """The running build still has the dropped-upper-bound filter bug.

    Recomputing on such a build rewrites every statement with FRESH wrong
    numbers — each figure summing from its period start to "now" — and marks
    them corrected. That exact failure happened in production: the dashboard
    (deployed separately) had the Fix Statement Totals button while the
    backend still ran a pre-fix build, so the button overwrote the stored
    totals with a new generation of unbounded-window values.
    """


def _filter_compiler_honors_ranges() -> bool:
    """Probe the live filter compiler with a two-sided range.

    A version check can't protect here — frontend and backend deploy
    independently, so the only trustworthy evidence is the behavior of the
    code actually running this process. The probe is pure and in-process:
    compile ``{"$gte": ..., "$lt": ...}`` against a recording stub and
    require BOTH bounds to come out.
    """
    try:
        from ..repositories._base import _apply_filters
    except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
        from repositories._base import _apply_filters  # type: ignore

    class _Probe:
        def __init__(self):
            self.ops: list[str] = []

        def _rec(self, op):
            self.ops.append(op)
            return self

        def gte(self, _c, _v):
            return self._rec("gte")

        def lt(self, _c, _v):
            return self._rec("lt")

        def eq(self, _c, _v):
            return self._rec("eq")

    probe = _Probe()
    try:
        _apply_filters(probe, {"created_at": {"$gte": "a", "$lt": "b"}})
    except Exception:  # noqa: BLE001 - any failure means "cannot trust this build"
        return False
    return "gte" in probe.ops and "lt" in probe.ops


# PostgREST caps an unbounded select at db-max-rows without signalling it.
PAGE_SIZE = 500

# Bound on one invocation so the HTTP entry point cannot run unboundedly long.
# The result reports whether more rows may remain rather than truncating
# silently (CLAUDE.md: no silent caps).
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000

SUPERSEDED_REASON = "dropped_upper_bound_filter_bug"


@dataclass
class TotalsChange:
    statement_id: str
    driver_id: str
    period_type: str
    period_start: str
    before: dict[str, Any]
    after: dict[str, Any]


@dataclass
class BackfillResult:
    applied: bool = False
    scanned: int = 0
    corrected: int = 0
    unchanged: int = 0
    has_more: bool = False
    changes: list[TotalsChange] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # Net movement across corrected rows, so the direction of the correction
    # can be sanity-checked before applying.
    delta_earnings: float = 0.0
    delta_payouts: float = 0.0


def parse_period_date(value: Any) -> date | None:
    """Accept a date, a 'YYYY-MM-DD' string, or a full ISO timestamp."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def money_keys(totals: dict | None) -> dict[str, Any]:
    """The three figures the job stores and the admin list renders."""
    t = totals or {}
    return {"earnings": t.get("earnings"), "payouts_total": t.get("payouts_total"), "trips": t.get("trips")}


def corrected_totals(old_totals: dict | None, statement: dict) -> dict[str, Any]:
    """New totals, preserving the pre-backfill figures for rollback/audit.

    ``superseded`` is written once. On a re-run the row is skipped as
    unchanged, and even if it were not, an existing ``superseded`` is never
    overwritten — an auditor needs the ORIGINAL job-time figures, not the
    previous run's.
    """
    out = {
        "earnings": statement["earnings"],
        "payouts_total": statement["payouts_total"],
        # Era split (may be absent from statements built before it existed;
        # the admin list falls back to the gross figure when missing).
        "payouts_spinr_total": statement.get("payouts_spinr_total"),
        "payouts_previous_app_total": statement.get("payouts_previous_app_total"),
        "trips": statement["trips"],
    }
    superseded = (old_totals or {}).get("superseded")
    if superseded is None:
        superseded = money_keys(old_totals)
        superseded["reason"] = SUPERSEDED_REASON
    out["superseded"] = superseded
    return out


async def load_statements(
    driver_ids: list[str] | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> tuple[list[dict], bool]:
    """Page through driver_statements. Returns (rows, has_more)."""
    filters: dict[str, Any] = {}
    if driver_ids:
        filters["driver_id"] = {"$in": driver_ids}
    if since:
        filters["period_start"] = {"$gte": since}

    rows: list[dict] = []
    offset = 0
    while True:
        page = (
            await db_supabase.get_rows(
                "driver_statements",
                filters,
                order="period_start",
                desc=True,
                limit=PAGE_SIZE,
                offset=offset,
            )
            or []
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows, False
        if limit and len(rows) >= limit:
            # More pages exist beyond the cap — say so instead of implying
            # the run covered everything.
            return rows[:limit], True
        offset += PAGE_SIZE


async def recompute_statement_totals(
    *,
    driver_ids: list[str] | None = None,
    since: str | None = None,
    limit: int | None = DEFAULT_LIMIT,
    apply: bool = False,
) -> BackfillResult:
    """Recompute (and optionally write) stored statement totals.

    ``apply=False`` is a pure read: the full diff is returned and nothing is
    written, which is what the admin UI previews before asking to confirm.

    Raises :class:`UnsafeBuildError` before touching anything when the running
    build would recompute with the very bug this backfill corrects.
    """
    if not _filter_compiler_honors_ranges():
        raise UnsafeBuildError(
            "This backend build still drops the upper bound of date-range filters, so the "
            "recompute would rewrite every statement with a fresh generation of wrong numbers "
            "(each summing from its period start to today). Deploy the build containing the "
            "filter-compiler fix, then run this again — nothing was changed."
        )
    if limit is not None:
        limit = max(1, min(int(limit), MAX_LIMIT))

    statements, has_more = await load_statements(driver_ids, since, limit)
    result = BackfillResult(applied=apply, scanned=len(statements), has_more=has_more)

    drivers: dict[str, dict] = {}

    for row in statements:
        sid = str(row.get("id"))
        driver_id = row.get("driver_id")
        period_type = (row.get("period_type") or "").strip()
        start_d = parse_period_date(row.get("period_start"))
        end_d = parse_period_date(row.get("period_end"))

        if not driver_id or start_d is None:
            result.skipped.append(f"{sid}: missing driver_id or unparseable period_start")
            continue

        driver = drivers.get(driver_id)
        if driver is None:
            found = await db_supabase.get_rows("drivers", {"id": driver_id}, limit=1)
            if not found:
                result.skipped.append(f"{sid}: driver no longer exists")
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
                result.skipped.append(f"{sid}: period_type {period_type!r} has no period_end to rebuild from")
                continue
        except Exception:
            logger.error("[STATEMENT-TOTALS] rebuild failed for %s", sid, exc_info=True)
            result.failed.append(sid)
            continue

        old = row.get("totals") or {}
        new = corrected_totals(old, statement)

        if money_keys(old) == money_keys(new):
            result.unchanged += 1
            continue

        result.corrected += 1

        def _earn_total(v: Any) -> float:
            # The job stores `earnings` as the full breakdown dict (with a
            # "total" key); treating it as a scalar made every earnings delta
            # silently skip, so previews reported earnings +0.00 no matter
            # how large the correction was.
            if isinstance(v, dict):
                v = v.get("total")
            return float(v or 0)

        try:
            result.delta_earnings += _earn_total(new["earnings"]) - _earn_total(old.get("earnings"))
            result.delta_payouts += float(new["payouts_total"]) - float(old.get("payouts_total") or 0)
        except (TypeError, ValueError):
            pass

        result.changes.append(
            TotalsChange(
                statement_id=sid,
                driver_id=str(driver_id),
                period_type=period_type,
                period_start=str(row.get("period_start")),
                before=money_keys(old),
                after=money_keys(new),
            )
        )

        if apply:
            try:
                # update_one returns None when it matched ZERO rows — a silent
                # no-op that would otherwise be counted as a correction.
                written = await db_supabase.update_one("driver_statements", {"id": sid}, {"totals": new})
                if not written:
                    logger.error("[STATEMENT-TOTALS] update matched no rows for %s", sid)
                    result.failed.append(sid)
            except Exception:
                logger.error("[STATEMENT-TOTALS] write failed for %s", sid, exc_info=True)
                result.failed.append(sid)

    logger.info(
        "[STATEMENT-TOTALS] %s scanned=%d corrected=%d unchanged=%d skipped=%d failed=%d has_more=%s",
        "applied" if apply else "dry-run",
        result.scanned,
        result.corrected,
        result.unchanged,
        len(result.skipped),
        len(result.failed),
        result.has_more,
    )
    return result
