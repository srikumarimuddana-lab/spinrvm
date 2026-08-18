"""Legacy driver-payout correction — DRY-RUN PLAN BUILDER ONLY.

Reconciles ``payments.csv`` from the previous (MongoDB-backed) app's export
against production ``rides``/``payouts`` to find driver earnings that were
imported (or are importable) via ``booking_import_service.py`` but were
**never actually paid out** in the old app — see
``docs/change-log/2026-08-15-legacy-payout-correction-plan.md`` for the full
Change Impact Log and the verified filter chain that produces the numbers
below.

Read-only through ``print_report()``: only answers "what would we do."

A write path exists below (``build_write_plan`` / ``commit_write_plan`` /
``fire_ready_transfers`` — see the "WRITE PATH" section further down this
file for the full design and scope), added 2026-08-17 on an explicit go-
ahead. It is not wired into any route, CLI entry point, or background loop
— every call is manual, and ``fire_ready_transfers`` (the only one that
touches Stripe) has never been invoked against production.

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
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from ..supabase_client import supabase
    from ..utils.money import dollars_to_cents, to_decimal
except ImportError:  # pragma: no cover - allow direct/CLI module imports
    from supabase_client import supabase  # type: ignore
    from utils.money import dollars_to_cents, to_decimal  # type: ignore

logger = logging.getLogger(__name__)

ZERO = Decimal("0")

# Payout type for the write path (build_write_plan / commit_write_plan /
# fire_ready_transfers below). Never a synthetic $0-net offset like
# 'legacy_import' -- this is real money, a real Stripe Transfer, for real
# unpaid legacy-app earnings. Treated like 'stripe_sync' everywhere a driver
# or the CRA sees it (statements, T4A): excluded from payable_balance math
# (the underlying legacy rides were already excluded from total_earnings, so
# including it there would double-subtract), but never hidden from the
# driver's own record of what they were paid, and always reported for T4A.
# See docs/change-log/2026-08-17-legacy-payout-correction-writepath.md §3a
# for why this differs from the original plan doc's tentative suggestion to
# reuse 'legacy_import'-style full exclusion.
PAYOUT_TYPE = "legacy_outstanding_correction"

# 2026-08-17 live re-verification (see
# docs/change-log/2026-08-17-legacy-payout-correction-writepath.md for the
# exact queries run) of the 3 Stripe-cross-check exclusions first identified
# in docs/change-log/2026-08-16-gst-backfill-and-stripe-crosscheck.md §1a
# (which only recorded truncated driver-id prefixes). Full UUIDs resolved by
# re-running this module's own filter chain against today's live `rides`.
#
# Kept as a named, dated exception list rather than re-derived by an
# automated heuristic: the underlying "likely already paid" / "ambiguous"
# calls are explicitly a human judgment -- the crosscheck doc's own words:
# "a 'confirmed' verdict is not reachable for any bucket with this schema;
# 'likely' is the ceiling." Baking that fuzzy matching into an algorithm
# risks silently misclassifying a future, different dataset. Same posture as
# migration 317's one named legacy exception (audit_logs_no_update): an
# explicit, reviewed, dated constant beats a clever re-derivation.
STRIPE_CROSSCHECK_EXCLUDED_DRIVER_IDS: dict[str, str] = {
    "a569909e-c866-4c5f-894e-e60489ca3593": "likely already paid -- clean payment->payout pair for the exact $22.43",
    "350b5267-7a4d-4548-bf4e-770ee22cb416": "ambiguous -- payment row with no matching payout anywhere ($33.32)",
    "93a899d5-431b-4743-afac-034cdf8c3d6c": "ambiguous -- two equally clean same-amount payment->payout pairs ($9.45)",
}

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
        # Kept as Decimal, not float(...) — this dict feeds print_report's
        # ${...:.2f} formatting directly (Decimal supports the same format
        # spec), and money must never pass through a float even display-only
        # (CLAUDE.md money-arithmetic convention).
        "group_a_amount": sum((r.payout_amount for r in a), ZERO),
        "group_b_rows": len(b),
        "group_b_old_drivers": len({r.old_driver_id for r in b}),
        "group_b_amount": sum((r.payout_amount for r in b), ZERO),
        "total_amount": sum((r.payout_amount for r in plan.rows), ZERO),
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


# ============================================================================
# WRITE PATH (docs/change-log/2026-08-17-legacy-payout-correction-writepath.md)
# ============================================================================
#
# Everything above this line is read-only, matching the original 2026-08-15
# plan. The three functions below are the write path §3a promised, deferred
# until now for an explicit go/no-go on the corrected design. Scope, per the
# 2026-08-17 product decision recorded in the Change Impact Log above:
#
#   - Group B (not-yet-imported bookings) is OUT OF SCOPE here -- it needs
#     the import-time carve-out `find_due_unpaid_rows`'s own docstring
#     describes, not a chase-and-fix after the fact. `build_write_plan`
#     reports it under `deferred_not_imported`, untouched.
#   - The 3 rows with no linked Spinr driver account stay blocked until
#     re-linked -- `excluded_unmatched_driver`.
#   - The 3 Stripe-cross-check-flagged buckets (STRIPE_CROSSCHECK_EXCLUDED_
#     DRIVER_IDS above) are excluded entirely, not paid, not held -- a human
#     follow-up, not this module's job.
#   - The 10 clean buckets with no Stripe Connect account on file are HELD
#     (`status='awaiting_stripe_onboarding'`), not skipped: the row is
#     written now so the debt is durably recorded, and `fire_ready_transfers`
#     picks it up automatically once/if that driver's account appears --
#     no re-run of this plan needed.
#   - The remaining clean buckets with a Stripe account already on file are
#     `status='ready_for_transfer'`.
#
# `commit_write_plan` never calls Stripe. `fire_ready_transfers` does, and is
# not invoked anywhere in this codebase (no route, no CLI, no background
# loop) -- it exists so the eventual real-money go-ahead is one explicit
# call, not a deployment. See its own docstring for the money-safety
# contract that call must satisfy.


@dataclass
class WriteRow:
    payout_id: str
    driver_id: str
    old_booking_id: str
    spinr_ride_id: str
    amount: Decimal
    status: str  # "ready_for_transfer" | "awaiting_stripe_onboarding"


@dataclass
class WritePlan:
    ready_rows: list[WriteRow] = field(default_factory=list)
    hold_rows: list[WriteRow] = field(default_factory=list)
    excluded_stripe_crosscheck: list[CorrectionRow] = field(default_factory=list)
    excluded_unmatched_driver: list[CorrectionRow] = field(default_factory=list)
    deferred_not_imported: list[CorrectionRow] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def payout_id_for(old_booking_id: str) -> str:
    """Deterministic payouts.id -- re-runs converge instead of duplicating.
    Matches stripe_payout_sync_service.payout_id_for's exact pattern."""
    return f"legacy-correction-{old_booking_id}"


def _fetch_driver_stripe_accounts(driver_ids: list[str]) -> dict[str, str]:
    """driver_id -> stripe_account_id, only for drivers with a non-empty one
    on file right now. Used both to classify ready-vs-hold at plan time and
    to resolve the real destination account at transfer time -- one query
    shape, no duplicated logic between the two."""
    if not driver_ids:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(driver_ids), 200):
        chunk = driver_ids[i : i + 200]
        rows = supabase.table("drivers").select("id, stripe_account_id").in_("id", chunk).execute().data or []
        for row in rows:
            acct = (row.get("stripe_account_id") or "").strip()
            if acct:
                out[row["id"]] = acct
    return out


def build_write_plan(plan: CorrectionPlan) -> WritePlan:
    """Classify every Group A row into ready / hold / excluded / deferred.

    Read-only: one `drivers` select for Stripe-account presence. Never
    writes to `payouts`, never calls Stripe.
    """
    write_plan = WritePlan()
    write_plan.deferred_not_imported = list(plan.group_b)

    eligible_rows: list[CorrectionRow] = []
    for row in plan.group_a:
        if not row.spinr_driver_id:
            write_plan.excluded_unmatched_driver.append(row)
            continue
        if row.spinr_driver_id in STRIPE_CROSSCHECK_EXCLUDED_DRIVER_IDS:
            write_plan.excluded_stripe_crosscheck.append(row)
            continue
        eligible_rows.append(row)

    driver_ids = sorted({r.spinr_driver_id for r in eligible_rows if r.spinr_driver_id})
    stripe_accounts = _fetch_driver_stripe_accounts(driver_ids)

    for row in eligible_rows:
        has_account = row.spinr_driver_id in stripe_accounts
        wr = WriteRow(
            payout_id=payout_id_for(row.old_booking_id),
            driver_id=row.spinr_driver_id,  # type: ignore[arg-type]  # non-None, filtered above
            old_booking_id=row.old_booking_id,
            spinr_ride_id=row.spinr_ride_id or "",
            amount=row.payout_amount,
            status="ready_for_transfer" if has_account else "awaiting_stripe_onboarding",
        )
        (write_plan.ready_rows if has_account else write_plan.hold_rows).append(wr)

    write_plan.stats = {
        "ready_rows": len(write_plan.ready_rows),
        "ready_amount": sum((r.amount for r in write_plan.ready_rows), ZERO),
        "hold_rows": len(write_plan.hold_rows),
        "hold_amount": sum((r.amount for r in write_plan.hold_rows), ZERO),
        "excluded_stripe_crosscheck_rows": len(write_plan.excluded_stripe_crosscheck),
        "excluded_stripe_crosscheck_amount": sum(
            (r.payout_amount for r in write_plan.excluded_stripe_crosscheck), ZERO
        ),
        "excluded_unmatched_driver_rows": len(write_plan.excluded_unmatched_driver),
        "excluded_unmatched_driver_amount": sum((r.payout_amount for r in write_plan.excluded_unmatched_driver), ZERO),
        "deferred_not_imported_rows": len(write_plan.deferred_not_imported),
        "deferred_not_imported_amount": sum((r.payout_amount for r in write_plan.deferred_not_imported), ZERO),
    }
    return write_plan


def print_write_report(write_plan: WritePlan) -> str:
    """Human-readable write-plan report. No side effects."""
    s = write_plan.stats
    buf = io.StringIO()
    w = buf.write
    w("LEGACY DRIVER-PAYOUT CORRECTION — WRITE PLAN (nothing committed yet)\n")
    w("=" * 70 + "\n")
    w(f"READY (Stripe account on file)         : {s['ready_rows']} rows, ${s['ready_amount']:.2f}\n")
    w(f"HELD (awaiting Stripe onboarding)      : {s['hold_rows']} rows, ${s['hold_amount']:.2f}\n")
    w(
        f"EXCLUDED (Stripe cross-check, 2026-08-16): {s['excluded_stripe_crosscheck_rows']} rows, "
        f"${s['excluded_stripe_crosscheck_amount']:.2f}\n"
    )
    w(
        f"EXCLUDED (no linked Spinr driver)      : {s['excluded_unmatched_driver_rows']} rows, "
        f"${s['excluded_unmatched_driver_amount']:.2f}\n"
    )
    w(
        f"DEFERRED (not yet imported, Group B)   : {s['deferred_not_imported_rows']} rows, "
        f"${s['deferred_not_imported_amount']:.2f}\n"
    )
    w("\nNo payouts table writes, no Stripe calls were made.\n")
    return buf.getvalue()


def _is_unique_violation(exc: Exception) -> bool:
    """Postgres 23505 via PostgREST/supabase-py. Matches
    stripe_payout_sync_service._is_unique_violation exactly."""
    return getattr(exc, "code", None) == "23505" or "duplicate key value" in str(exc)


def commit_write_plan(write_plan: WritePlan) -> dict[str, Any]:
    """Insert payouts rows for BOTH ready and held rows -- the debt is
    recorded either way. Never calls Stripe; that is the separate, explicit
    `fire_ready_transfers` step below.

    Deterministic ids (`payout_id_for`) make this idempotent: a row that
    landed in a previous or concurrent run raises 23505 on its batch insert,
    which falls back to row-by-row inserts that skip duplicates -- exact
    same recovery pattern as stripe_payout_sync_service.commit_plan.
    """
    all_rows = write_plan.ready_rows + write_plan.hold_rows
    now_iso = datetime.now(timezone.utc).isoformat()
    to_insert = [
        {
            "id": r.payout_id,
            "driver_id": r.driver_id,
            # str() only at the serialization boundary (payouts.amount is
            # NUMERIC as of migration 331 -- str(Decimal) round-trips exact,
            # unlike float() which reintroduces binary rounding error before
            # Postgres ever sees the value). All arithmetic above stayed
            # Decimal.
            "amount": str(r.amount),
            "status": r.status,
            "payout_type": PAYOUT_TYPE,
            "bank_name": "Legacy outstanding-earnings correction",
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        for r in all_rows
    ]
    inserted = 0
    skipped_existing = 0
    for i in range(0, len(to_insert), 200):
        chunk = to_insert[i : i + 200]
        try:
            supabase.table("payouts").insert(chunk).execute()
            inserted += len(chunk)
        except Exception as e:
            if not _is_unique_violation(e):
                raise
            for row in chunk:
                try:
                    supabase.table("payouts").insert(row).execute()
                    inserted += 1
                except Exception as row_e:
                    if _is_unique_violation(row_e):
                        skipped_existing += 1
                        continue
                    raise
    if skipped_existing:
        logger.info(
            "[LEGACY-PAYOUT-CORRECTION] %d row(s) already present (concurrent/previous run), skipped",
            skipped_existing,
        )
    return {"inserted": inserted, "skipped_existing": skipped_existing}


def fire_ready_transfers(stripe_secret: str) -> dict[str, Any]:
    """Fire one real Stripe Transfer per committed row still
    status='ready_for_transfer'. NOT called anywhere in this codebase --
    exists so the eventual go-ahead is a single explicit call, not a new
    deployment.

    Money-safety contract, mirroring routes/drivers/payouts.py's instant-
    payout Step 1 exactly (platform -> connect account transfer), but with
    no Step 2: this money lands in the driver's Connect balance and cashes
    out through their own normal standard/instant payout later, exactly like
    `stripe_sync`-synced legacy money already does. Per-row idempotency key
    (`legacy-correction-transfer-{payout_id}`) means a retry on the same row
    can never double-transfer. The row's status is only ever advanced
    forward (never re-tried automatically after a terminal 'failed' or
    'completed') -- a failed row needs a human look, not a silent retry loop
    moving money on its own schedule.

    A driver whose Stripe account disappeared between commit and this call
    (detached, re-onboarding) is not a doomed Transfer attempt -- falls back
    to 'awaiting_stripe_onboarding' so the next run picks it up once/if the
    account returns.
    """
    import stripe

    rows = (
        supabase.table("payouts")
        .select("id, driver_id, amount")
        .eq("payout_type", PAYOUT_TYPE)
        .eq("status", "ready_for_transfer")
        .execute()
        .data
        or []
    )
    driver_ids = sorted({r["driver_id"] for r in rows})
    accounts = _fetch_driver_stripe_accounts(driver_ids) if rows else {}

    fired = 0
    failed: list[str] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for row in rows:
        payout_id = row["id"]
        account_id = accounts.get(row["driver_id"])
        if not account_id:
            supabase.table("payouts").update({"status": "awaiting_stripe_onboarding", "updated_at": now_iso}).eq(
                "id", payout_id
            ).execute()
            continue
        try:
            transfer = stripe.Transfer.create(
                amount=dollars_to_cents(to_decimal(row["amount"])),
                currency="cad",
                destination=account_id,
                api_key=stripe_secret,
                idempotency_key=f"legacy-correction-transfer-{payout_id}",
            )
            supabase.table("payouts").update(
                {
                    "status": "completed",
                    "stripe_transfer_id": transfer.id,
                    "processed_at": now_iso,
                    "updated_at": now_iso,
                }
            ).eq("id", payout_id).execute()
            fired += 1
        except Exception as e:
            logger.error("[LEGACY-PAYOUT-CORRECTION] transfer failed for payout_id=%s: %s", payout_id, e, exc_info=True)
            supabase.table("payouts").update(
                {"status": "failed", "failure_reason": str(e)[:500], "updated_at": now_iso}
            ).eq("id", payout_id).execute()
            failed.append(payout_id)
    return {"fired": fired, "failed": failed}
