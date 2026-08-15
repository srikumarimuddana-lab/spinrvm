"""Legacy driver-payout correction — DRY-RUN PLAN BUILDER ONLY.

Reconciles ``payments.csv`` from the previous (MongoDB-backed) app's export
against production ``rides``/``payouts`` to find driver earnings that were
imported (or are importable) via ``booking_import_service.py`` but were
**never actually paid out** in the old app — see
``docs/change-log/2026-08-15-legacy-payout-correction-plan.md`` for the full
Change Impact Log and the verified filter chain that produces the numbers
below.

Deliberately provides no ``commit_plan()`` / write path. This module only
answers "what would we do" — inserting rows, initiating Stripe transfers, and
recounting driver balances are separate, later steps that need an explicit
go/no-go on the *plan* this module prints, not a code change to enable.

## Why two groups

``booking_import_service.py`` pairs every imported ride's earnings with an
offsetting ``payouts`` row (``payout_type='legacy_import'``, status
``'completed'``) on the assumption "already settled in the previous app."
That assumption is false for the driver-payout rows this module finds
(``payments.pending_amount_status == 'due'``):

- **Group A** — booking already imported into ``rides`` (so it already has a
  ``legacy_import`` offset that wrongly zeroed the driver's live balance for
  real, unpaid money). Needs an *additive* correction payout — the existing
  offset row is never touched or deleted.
- **Group B** — booking not yet imported at all. If it goes through the
  ordinary importer unmodified, it would get the standard full offset and the
  same problem recurs. Needs a carve-out at import time, not a chase-and-fix
  after the fact.

## Filter chain (see the change-log doc for full detail)

    payments.csv (372 rows)
      -> pending_amount_status == 'due'                          (158 rows)
      -> booking_id exists in bookings.csv                       ( 50 rows)
      -> exclude test/non-CA tenant (driver OR customer
         country_code == '91', or email ends '@yopmail.com')     ( 35 rows)
      -> group by driver_id, split A/B against live `rides`      (35 rows / 20 drivers)

The 108 rows dropped at the first join are not confirmed as real or fake —
their ObjectIDs embed timestamps (2025-11-21..2026-01-29) predating every
other export file (2026-01-30 onward), so they cannot be cross-referenced
from these CSVs at all. Flagged, not counted, not discarded as "resolved."

Reports carry only ``old_booking_id`` / ``driver_id`` (Spinf UUID) / amounts
— never rider or driver names, phones, or addresses, matching the PII policy
every other ``*_import_service.py`` in this repo follows.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from ..supabase_client import supabase
    from ..utils.money import to_decimal
except ImportError:  # pragma: no cover - allow direct/CLI module imports
    from supabase_client import supabase  # type: ignore
    from utils.money import to_decimal  # type: ignore

ZERO = Decimal("0")

DUE_STATUS = "due"
TEST_COUNTRY_CODE = "91"
TEST_EMAIL_SUFFIX = "@yopmail.com"

# Batch-scoped so a re-run is a plain equality check, matching
# booking_import_service.payout_id_for's deterministic-ID pattern. The batch
# string is set once real remediation is scheduled; the plan builder itself
# does not need it (no writes to key off).


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header row: {path}")
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k is not None} for row in reader]


def _index_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["_id"]: r for r in rows if r.get("_id")}


def _is_test_tenant(driver_id: str, customer_id: str, drivers_by_id: dict, customers_by_id: dict) -> bool:
    """Vendor test/non-Canadian accounts — see booking_import_service's
    CANADA_COUNTRY_CODE comment for the same convention applied here."""
    for row in (drivers_by_id.get(driver_id, {}), customers_by_id.get(customer_id, {})):
        if (row.get("country_code") or "").strip() == TEST_COUNTRY_CODE:
            return True
        if (row.get("email") or "").strip().lower().endswith(TEST_EMAIL_SUFFIX):
            return True
    return False


@dataclass
class CorrectionRow:
    old_booking_id: str
    old_driver_id: str
    payout_amount: Decimal
    already_imported: bool
    spinr_ride_id: str | None = None
    spinr_driver_id: str | None = None


@dataclass
class CorrectionPlan:
    rows: list[CorrectionRow] = field(default_factory=list)
    unresolved_row_count: int = 0  # rows dropped at the booking-existence join
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def group_a(self) -> list[CorrectionRow]:
        """Already imported — needs an additive correction payout per driver."""
        return [r for r in self.rows if r.already_imported]

    @property
    def group_b(self) -> list[CorrectionRow]:
        """Not yet imported — needs the import-time carve-out."""
        return [r for r in self.rows if not r.already_imported]


def find_due_unpaid_rows(
    payments_rows: list[dict[str, str]],
    bookings_by_id: dict[str, dict[str, str]],
    drivers_by_id: dict[str, dict[str, str]],
    customers_by_id: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    """Apply the verified filter chain. Returns (kept_rows, unresolved_count)."""
    due = [r for r in payments_rows if (r.get("pending_amount_status") or "").strip() == DUE_STATUS]
    unresolved = 0
    kept: list[dict[str, str]] = []
    for r in due:
        bid = (r.get("booking_id") or "").strip()
        if bid not in bookings_by_id:
            unresolved += 1
            continue
        if _is_test_tenant(r.get("driver_id", ""), r.get("customer_id", ""), drivers_by_id, customers_by_id):
            continue
        kept.append(r)
    return kept, unresolved


def _fetch_already_imported(old_booking_ids: list[str]) -> dict[str, tuple[str, str | None]]:
    """old_booking_id -> (spinr_ride_id, spinr_driver_id) for rows already in
    production `rides`. Empty dict if none — never raises on zero matches."""
    if not old_booking_ids:
        return {}
    out: dict[str, tuple[str, str | None]] = {}
    for i in range(0, len(old_booking_ids), 200):
        chunk = old_booking_ids[i : i + 200]
        rows = (
            supabase.table("rides")
            .select("id, driver_id, legacy_import_metadata")
            .filter("legacy_import_metadata->>old_booking_id", "in", f"({','.join(chunk)})")
            .execute()
            .data
            or []
        )
        for row in rows:
            old_id = (row.get("legacy_import_metadata") or {}).get("old_booking_id")
            if old_id:
                out[old_id] = (row["id"], row.get("driver_id"))
    return out


def build_correction_plan(
    payments_csv: Path | str,
    bookings_csv: Path | str,
    drivers_csv: Path | str,
    customers_csv: Path | str,
) -> CorrectionPlan:
    """Read the 4 legacy CSVs, apply the filter chain, cross-check against
    live `rides`, and return a plan. Read-only end to end — this function
    never writes to Supabase or calls Stripe."""
    payments_rows = _read_csv_rows(Path(payments_csv))
    bookings_by_id = _index_by_id(_read_csv_rows(Path(bookings_csv)))
    drivers_by_id = _index_by_id(_read_csv_rows(Path(drivers_csv)))
    customers_by_id = _index_by_id(_read_csv_rows(Path(customers_csv)))

    kept, unresolved = find_due_unpaid_rows(payments_rows, bookings_by_id, drivers_by_id, customers_by_id)

    old_booking_ids = [r["booking_id"] for r in kept]
    imported = _fetch_already_imported(old_booking_ids)

    plan = CorrectionPlan(unresolved_row_count=unresolved)
    for r in kept:
        bid = r["booking_id"]
        amount = to_decimal(r.get("payout_amount") or "0")
        ride_id, spinr_driver_id = imported.get(bid, (None, None))
        plan.rows.append(
            CorrectionRow(
                old_booking_id=bid,
                old_driver_id=r.get("driver_id", ""),
                payout_amount=amount,
                already_imported=bid in imported,
                spinr_ride_id=ride_id,
                spinr_driver_id=spinr_driver_id,
            )
        )

    a, b = plan.group_a, plan.group_b
    plan.stats = {
        "payments_rows_total": len(payments_rows),
        "due_rows_before_join": len([r for r in payments_rows if (r.get("pending_amount_status") or "") == DUE_STATUS]),
        "unresolved_dropped": unresolved,
        "kept_after_filters": len(kept),
        "group_a_rows": len(a),
        "group_a_drivers": len({r.spinr_driver_id for r in a if r.spinr_driver_id}),
        "group_a_amount": float(sum((r.payout_amount for r in a), ZERO)),
        "group_b_rows": len(b),
        "group_b_old_drivers": len({r.old_driver_id for r in b}),
        "group_b_amount": float(sum((r.payout_amount for r in b), ZERO)),
        "total_amount": float(sum((r.payout_amount for r in plan.rows), ZERO)),
    }
    return plan


def print_report(plan: CorrectionPlan) -> str:
    """Human-readable dry-run report. No side effects."""
    s = plan.stats
    buf = io.StringIO()
    w = buf.write
    w("LEGACY DRIVER-PAYOUT CORRECTION — DRY RUN (no writes performed)\n")
    w("=" * 65 + "\n")
    w(f"payments.csv rows scanned      : {s['payments_rows_total']}\n")
    w(f"pending_amount_status='due'    : {s['due_rows_before_join']}\n")
    w(f"  -> unresolved (no booking match, unverifiable from CSVs) : {s['unresolved_dropped']}\n")
    w(f"  -> kept after booking + tenant filters                  : {s['kept_after_filters']}\n")
    w("\n")
    w("GROUP A (already imported into rides -- additive correction payout needed)\n")
    w(f"  rows    : {s['group_a_rows']}\n")
    w(f"  drivers : {s['group_a_drivers']}\n")
    w(f"  amount  : ${s['group_a_amount']:.2f}\n")
    for r in plan.group_a:
        w(
            f"    old_booking={r.old_booking_id} ride={r.spinr_ride_id} driver={r.spinr_driver_id} amount=${r.payout_amount}\n"
        )
    w("\n")
    w("GROUP B (not yet imported -- needs import-time carve-out, no full offset)\n")
    w(f"  rows          : {s['group_b_rows']}\n")
    w(f"  old driver IDs: {s['group_b_old_drivers']}\n")
    w(f"  amount        : ${s['group_b_amount']:.2f}\n")
    for r in plan.group_b:
        w(f"    old_booking={r.old_booking_id} old_driver={r.old_driver_id} amount=${r.payout_amount}\n")
    w("\n")
    w(f"TOTAL: ${s['total_amount']:.2f} across {s['group_a_rows'] + s['group_b_rows']} rows\n")
    w("\nNo payouts table writes, no ride inserts, no Stripe calls were made.\n")
    return buf.getvalue()
