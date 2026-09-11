"""D1 `tax_amount` correction for the 186 legacy-imported rides — DRY-RUN
PLAN BUILDER ONLY.

Background: `booking_import_service.py` wrote `tax_amount` from the legacy
export's `gst` column, which is `commission_gst_amount` (GST on Spinr's own
platform commission) — not GST on the rider-facing fare. The correct,
rider-facing figure was separately preserved (2026-08-16,
`docs/change-log/2026-08-16-gst-backfill-executed.md`) as
`legacy_import_metadata->>'old_payout_gst_amount'` on all 186 affected rows.
See `docs/change-log/2026-08-15-legacy-import-gst-preservation.md` for the
full root-cause finding. Riders were charged and shown the correct amount at
the time (`total_amount`/`grand_total` are untouched by this); this is a
backend bookkeeping correction only, not a pricing or receipt error.

**Product-owner decision (2026-09-07, ACTION_ITEMS.md D1):** fix
`tax_amount` for the 186 rows using the preserved `old_payout_gst_amount`
figure, but validate it as part of the run rather than copy it blind. This
module is that validation + plan step. Deliberately narrow, same posture as
every other legacy-migration tool in this repo:

- **Read-only end to end.** Never writes to Supabase. Builds a plan; a
  separate human with `DATABASE_URL` production access (which this
  environment does not have) reviews it and runs the emitted SQL.
- **Validates, does not blindly trust, `old_payout_gst_amount`.** Two
  independent checks per row:
  1. **Re-derive from `bookings.csv` and compare.** If the value stored in
     `legacy_import_metadata` no longer matches what the source CSV says,
     the row is BLOCKED (flagged, not silently applied) — something drifted
     since the 2026-08-16 backfill and needs a human look before writing.
  2. **5%-of-`total_fare` sanity check.** GST is 5% of the pre-tax fare.
     `total_fare` is the closest already-stored approximation of that base
     for a legacy row (see `booking_import_service.py`'s residual-fare
     comment — legacy rows have no separate distance/time split). This
     check is advisory only, not blocking: the old app's exact GST base
     isn't confirmed to equal `total_fare`, so a row outside tolerance is
     flagged for a human to eyeball, not excluded from the plan.
- **No commit path.** `render_update_sql()` renders the guarded SQL text for
  a human to review and run — it does not execute anything itself, and
  takes no real production data as a module-level constant (nothing here is
  ever committed with real ride IDs/dollar amounts baked in).

Idempotent by construction: a row whose current `tax_amount` already equals
the validated `old_payout_gst_amount` is still included in the plan (so the
report always reflects the full 186), but its delta is $0 — the emitted SQL
still guards on the row's current `tax_amount` matching what this plan saw,
so a stale/already-applied row can't be double-written.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from ..services.booking_import_service import IMPORT_SOURCE
    from ..supabase_client import supabase
    from ..utils.money import to_decimal
except ImportError:  # pragma: no cover - allow direct/CLI module imports
    from services.booking_import_service import IMPORT_SOURCE  # type: ignore
    from supabase_client import supabase  # type: ignore
    from utils.money import to_decimal  # type: ignore

ZERO = Decimal("0")
GST_RATE = Decimal("0.05")
# Sanity-check tolerance: the greater of $0.10 or 25% relative deviation from
# 5% of total_fare. Wide on purpose -- total_fare is an approximation of the
# old app's real GST base, not a confirmed exact match, so this is a flag for
# human attention, not a pass/fail gate.
SANITY_ABS_TOLERANCE = Decimal("0.10")
SANITY_REL_TOLERANCE = Decimal("0.25")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header row: {path}")
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k is not None} for row in reader]


def _index_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["_id"]: r for r in rows if r.get("_id")}


@dataclass
class BackfillRow:
    ride_id: str
    old_booking_id: str | None
    old_tax_amount: Decimal  # current (wrong-base) rides.tax_amount
    new_tax_amount: Decimal  # validated old_payout_gst_amount, proposed replacement
    total_fare: Decimal
    blocked_reason: str | None  # None => applyable; else why it's excluded
    sanity_flag: bool  # True => outside the 5%-of-total_fare tolerance (advisory only)

    @property
    def applyable(self) -> bool:
        return self.blocked_reason is None

    @property
    def delta(self) -> Decimal:
        return self.new_tax_amount - self.old_tax_amount


@dataclass
class BackfillPlan:
    rows: list[BackfillRow] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def _fetch_legacy_rows() -> list[dict[str, Any]]:
    """Every legacy-imported ride, with the fields this plan needs.
    Read-only."""
    out: list[dict[str, Any]] = []
    offset = 0
    page = 500
    while True:
        rows = (
            supabase.table("rides")
            .select("id, legacy_import_metadata, tax_amount, total_fare")
            .filter("legacy_import_metadata->>source", "eq", IMPORT_SOURCE)
            .range(offset, offset + page - 1)
            .execute()
            .data
            or []
        )
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


def build_backfill_plan(bookings_csv: Path | str) -> BackfillPlan:
    """Read bookings.csv, fetch every legacy-imported ride, validate the
    preserved old_payout_gst_amount against the source CSV, and produce a
    plan to correct tax_amount for the rows that pass validation. Read-only
    end to end -- never writes to Supabase."""
    bookings_by_id = _index_by_id(_read_csv_rows(Path(bookings_csv)))
    candidates = _fetch_legacy_rows()

    plan = BackfillPlan()
    for r in candidates:
        meta = r.get("legacy_import_metadata") or {}
        old_id = meta.get("old_booking_id")
        old_tax_amount = to_decimal(str(r.get("tax_amount") or "0"))
        total_fare = to_decimal(str(r.get("total_fare") or "0"))

        if "old_payout_gst_amount" not in meta:
            plan.rows.append(
                BackfillRow(
                    ride_id=r["id"],
                    old_booking_id=old_id,
                    old_tax_amount=old_tax_amount,
                    new_tax_amount=old_tax_amount,
                    total_fare=total_fare,
                    blocked_reason="not_yet_backfilled",
                    sanity_flag=False,
                )
            )
            continue

        stored_amount = to_decimal(str(meta.get("old_payout_gst_amount")))
        booking = bookings_by_id.get(old_id) if old_id else None
        if not booking:
            plan.rows.append(
                BackfillRow(
                    ride_id=r["id"],
                    old_booking_id=old_id,
                    old_tax_amount=old_tax_amount,
                    new_tax_amount=old_tax_amount,
                    total_fare=total_fare,
                    blocked_reason="csv_unresolved",
                    sanity_flag=False,
                )
            )
            continue

        csv_amount = to_decimal(booking.get("payout_gst_amount") or "0")
        if csv_amount != stored_amount:
            plan.rows.append(
                BackfillRow(
                    ride_id=r["id"],
                    old_booking_id=old_id,
                    old_tax_amount=old_tax_amount,
                    new_tax_amount=old_tax_amount,
                    total_fare=total_fare,
                    blocked_reason="csv_drift",
                    sanity_flag=False,
                )
            )
            continue

        expected = (total_fare * GST_RATE).quantize(Decimal("0.01"))
        tolerance = max(SANITY_ABS_TOLERANCE, expected * SANITY_REL_TOLERANCE)
        sanity_flag = abs(csv_amount - expected) > tolerance

        plan.rows.append(
            BackfillRow(
                ride_id=r["id"],
                old_booking_id=old_id,
                old_tax_amount=old_tax_amount,
                new_tax_amount=csv_amount,
                total_fare=total_fare,
                blocked_reason=None,
                sanity_flag=sanity_flag,
            )
        )

    applyable = [row for row in plan.rows if row.applyable]
    blocked = [row for row in plan.rows if not row.applyable]
    plan.stats = {
        "candidate_rides": len(candidates),
        "applyable": len(applyable),
        "blocked_not_yet_backfilled": sum(1 for row in blocked if row.blocked_reason == "not_yet_backfilled"),
        "blocked_csv_unresolved": sum(1 for row in blocked if row.blocked_reason == "csv_unresolved"),
        "blocked_csv_drift": sum(1 for row in blocked if row.blocked_reason == "csv_drift"),
        "sanity_flagged": sum(1 for row in applyable if row.sanity_flag),
        "sum_old_tax_amount": sum((row.old_tax_amount for row in applyable), ZERO),
        "sum_new_tax_amount": sum((row.new_tax_amount for row in applyable), ZERO),
        "sum_delta": sum((row.delta for row in applyable), ZERO),
    }
    return plan


def print_report(plan: BackfillPlan) -> str:
    """Human-readable dry-run report. No side effects."""
    s = plan.stats
    buf = io.StringIO()
    w = buf.write
    w("D1 tax_amount CORRECTION — DRY RUN (no writes performed)\n")
    w("=" * 72 + "\n")
    w(f"legacy-imported candidate rides            : {s['candidate_rides']}\n")
    w(f"  -> applyable (validated)                 : {s['applyable']}\n")
    w(f"     of which sanity-outlier (advisory)    : {s['sanity_flagged']}\n")
    w(f"  -> blocked: not yet GST-backfilled        : {s['blocked_not_yet_backfilled']}\n")
    w(f"  -> blocked: no bookings.csv match         : {s['blocked_csv_unresolved']}\n")
    w(f"  -> blocked: stored value != CSV (drift)   : {s['blocked_csv_drift']}\n")
    w(f"sum tax_amount BEFORE (applyable rows)     : ${s['sum_old_tax_amount']:.2f}\n")
    w(f"sum tax_amount AFTER  (applyable rows)     : ${s['sum_new_tax_amount']:.2f}\n")
    w(f"net delta                                  : ${s['sum_delta']:.2f}\n")
    w("\n")
    blocked_rows = [row for row in plan.rows if not row.applyable]
    if blocked_rows:
        w("Blocked rows (flagged, not silently skipped):\n")
        for row in blocked_rows:
            w(f"    ride={row.ride_id} old_booking={row.old_booking_id} reason={row.blocked_reason}\n")
        w("\n")
    outliers = [row for row in plan.rows if row.applyable and row.sanity_flag]
    if outliers:
        w("Sanity-outlier rows (advisory only, not blocked -- eyeball before applying):\n")
        for row in outliers:
            w(
                f"    ride={row.ride_id} new_tax_amount=${row.new_tax_amount:.2f} "
                f"total_fare=${row.total_fare:.2f} (5% would be "
                f"${(row.total_fare * GST_RATE).quantize(Decimal('0.01')):.2f})\n"
            )
        w("\n")
    w("This plan proposes rides.tax_amount = validated old_payout_gst_amount\n")
    w("for applyable rows only. It does NOT itself write to the database --\n")
    w("see render_update_sql() for the guarded SQL a human runs separately.\n")
    w("\nNo rides table writes were made.\n")
    return buf.getvalue()


def render_update_sql(plan: BackfillPlan) -> str:
    """Render (never execute) the guarded SQL UPDATE for the plan's
    applyable rows. Optimistic-concurrency guarded on the row's tax_amount
    still matching what this plan observed, so a row changed since the plan
    was built is skipped rather than silently overwritten. Contains real
    plan data when called against a real plan -- this function's OUTPUT is
    not meant to be committed to the repo; only this generator is."""
    applyable = [row for row in plan.rows if row.applyable]
    if not applyable:
        return "-- No applyable rows in this plan; nothing to update.\n"

    values_lines = []
    for row in applyable:
        breakdown = {"GST": {"rate": 5.0, "amount": float(row.new_tax_amount)}} if row.new_tax_amount > ZERO else {}
        values_lines.append(
            "  ('{ride_id}', {old:.2f}, {new:.2f}, '{breakdown}'::jsonb)".format(
                ride_id=row.ride_id,
                old=row.old_tax_amount,
                new=row.new_tax_amount,
                breakdown=json.dumps(breakdown).replace("'", "''"),
            )
        )

    buf = io.StringIO()
    w = buf.write
    w("-- D1 tax_amount correction -- review before running.\n")
    w("-- Guarded on r.tax_amount still matching what this plan observed,\n")
    w("-- so a row changed since the plan was built is skipped, not clobbered.\n")
    w("UPDATE rides r\n")
    w("SET tax_amount = v.new_tax_amount,\n")
    w("    tax_breakdown = v.new_tax_breakdown\n")
    w("FROM (VALUES\n")
    w(",\n".join(values_lines))
    w("\n) AS v(id, old_tax_amount, new_tax_amount, new_tax_breakdown)\n")
    w("WHERE r.id = v.id::text\n")
    w("  AND r.legacy_import_metadata->>'source' = 'legacy_mongo_booking_import'\n")
    w("  AND r.tax_amount = v.old_tax_amount\n")
    w("RETURNING r.id;\n")
    return buf.getvalue()
