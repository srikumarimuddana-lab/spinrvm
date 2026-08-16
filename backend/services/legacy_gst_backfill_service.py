"""Legacy `old_payout_gst_amount` backfill — DRY-RUN PLAN BUILDER ONLY.

``booking_import_service.py`` was fixed (2026-08-16, PR #3963) to preserve
the previously-dropped ``payout_gst_amount`` column from the legacy export
as ``legacy_import_metadata->>'old_payout_gst_amount'`` on every *newly*
imported row. That fix does nothing for rows imported before it shipped —
confirmed live: 0 of the 186 already-migrated rides in production carry the
field.

This module closes that gap for the already-migrated rows, and ONLY that
gap. It is deliberately narrow:

- **Additive only.** Writes exactly one new JSONB key
  (``legacy_import_metadata->>'old_payout_gst_amount'``) per row. Never
  touches ``tax_amount``, ``tax_breakdown``, or any other field.
- **Does not decide the tax-treatment question.** What the *correct*
  historical rider-facing GST figure should be for these rows — leave
  ``tax_amount`` as commission-GST, backfill an estimate, or wait for a
  legal/finance answer — is an open business decision (see
  ``docs/change-log/2026-08-15-legacy-payout-correction-plan.md`` §1.3
  addendum). This backfill is intentionally independent of that decision:
  whichever way it goes, having the raw number already in Supabase instead
  of needing someone to re-parse the CSV again later is strictly useful and
  carries zero risk on its own.
- **No commit path.** Same posture as every other legacy-migration tool in
  this repo this week: build a plan, print it, let a human decide whether
  and when to apply it. Inserting the actual UPDATE is a separate, later
  step.

Idempotent by construction: only selects rows where
``legacy_import_metadata`` does NOT already have the key, so a second run
after some rows already got it (e.g. from a future import batch, or a
partial prior application) is a no-op for those rows, not a re-write.
"""

from __future__ import annotations

import csv
import io
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
    old_booking_id: str
    old_payout_gst_amount: Decimal
    found_in_source: bool  # False = ride is legacy-imported but old_booking_id has no match in bookings.csv


@dataclass
class BackfillPlan:
    rows: list[BackfillRow] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def _fetch_rows_missing_field() -> list[dict[str, Any]]:
    """Every already-migrated ride that does NOT yet carry
    old_payout_gst_amount in its metadata. Read-only."""
    out: list[dict[str, Any]] = []
    offset = 0
    page = 500
    while True:
        rows = (
            supabase.table("rides")
            .select("id, legacy_import_metadata")
            .filter("legacy_import_metadata->>source", "eq", IMPORT_SOURCE)
            .range(offset, offset + page - 1)
            .execute()
            .data
            or []
        )
        if not rows:
            break
        for r in rows:
            meta = r.get("legacy_import_metadata") or {}
            if "old_payout_gst_amount" not in meta:
                out.append(r)
        if len(rows) < page:
            break
        offset += page
    return out


def build_backfill_plan(bookings_csv: Path | str) -> BackfillPlan:
    """Read bookings.csv, find every already-migrated ride missing
    old_payout_gst_amount, and produce a plan to add it. Read-only end to
    end — never writes to Supabase."""
    bookings_by_id = _index_by_id(_read_csv_rows(Path(bookings_csv)))
    candidates = _fetch_rows_missing_field()

    plan = BackfillPlan()
    for r in candidates:
        meta = r.get("legacy_import_metadata") or {}
        old_id = meta.get("old_booking_id")
        if not old_id:
            continue  # not actually a legacy-import row with a resolvable source booking
        booking = bookings_by_id.get(old_id)
        if not booking:
            plan.rows.append(
                BackfillRow(
                    ride_id=r["id"],
                    old_booking_id=old_id,
                    old_payout_gst_amount=ZERO,
                    found_in_source=False,
                )
            )
            continue
        amount = to_decimal(booking.get("payout_gst_amount") or "0")
        plan.rows.append(
            BackfillRow(
                ride_id=r["id"],
                old_booking_id=old_id,
                old_payout_gst_amount=amount,
                found_in_source=True,
            )
        )

    found = [r for r in plan.rows if r.found_in_source]
    not_found = [r for r in plan.rows if not r.found_in_source]
    plan.stats = {
        "candidate_rides_missing_field": len(candidates),
        "resolvable_against_source": len(found),
        "unresolvable_no_source_match": len(not_found),
        "sum_old_payout_gst_amount": float(sum((r.old_payout_gst_amount for r in found), ZERO)),
    }
    return plan


def print_report(plan: BackfillPlan) -> str:
    """Human-readable dry-run report. No side effects."""
    s = plan.stats
    buf = io.StringIO()
    w = buf.write
    w("LEGACY old_payout_gst_amount BACKFILL — DRY RUN (no writes performed)\n")
    w("=" * 72 + "\n")
    w(f"already-migrated rides missing the field : {s['candidate_rides_missing_field']}\n")
    w(f"  -> resolvable against bookings.csv     : {s['resolvable_against_source']}\n")
    w(f"  -> NOT resolvable (no source match)    : {s['unresolvable_no_source_match']}\n")
    w(f"sum of old_payout_gst_amount to backfill : ${s['sum_old_payout_gst_amount']:.2f}\n")
    w("\n")
    if s["unresolvable_no_source_match"]:
        w("Rides with no source match (flagged, not silently skipped):\n")
        for r in plan.rows:
            if not r.found_in_source:
                w(f"    ride={r.ride_id} old_booking={r.old_booking_id}\n")
        w("\n")
    w("This plan would set exactly one new JSONB key per resolvable row:\n")
    w("  legacy_import_metadata->>'old_payout_gst_amount' = <source value>\n")
    w("It does NOT change tax_amount, tax_breakdown, or any other field.\n")
    w("\nNo rides table writes were made.\n")
    return buf.getvalue()
