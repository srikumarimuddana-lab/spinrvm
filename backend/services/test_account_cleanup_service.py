"""Sanctioned test-account cleanup — PLAN BUILDER ONLY, no write path (A35).

An ad-hoc, hand-written SQL script (found via `pg_stat_statements`, never a
file in this repo — run directly against Postgres, bypassing every guard
this codebase has) disabled the append-only regulatory triggers on
`driver_insurance_periods`/`financial_events`/`audit_logs` in order to
hard-delete a phone-scoped set of accounts. The 2026-08-14 runs targeted
genuine pre-launch test accounts and were confirmed benign — but the script
itself had no eligibility check at all, unlike the sanctioned
`purge_pii_retention()` Step H (`backend/migrations/216_deletion_hard_delete_no_anonymize.sql`
onward), which explicitly refuses to hard-delete any account carrying
`rides`, `driver_insurance_periods`, `payouts`, or `bank_accounts` rows. Reused
carelessly, the ad-hoc script would do the same thing to a real driver's
regulated insurance history (`docs/audit/2026-08-16-legacy-ride-count-drop-investigation.md`,
A35 in `ACTION_ITEMS.md`).

This module exists so nobody needs to hand-write that SQL again. It mirrors
Step H's exact eligibility guard for a phone-scoped batch:

- **Never disables a trigger. Never issues a DELETE.** Plan-only, same
  posture as every other legacy-migration tool in this repo
  (`legacy_payout_correction_service.py`, `legacy_gst_backfill_service.py`) —
  build a plan, print it, let a human decide. Wiring an actual delete step is
  a separate, later, explicitly-gated change.
- **Hard-fails per account, loudly — never silently skips.** Every matched
  account is bucketed into exactly one of `safe_to_delete` or
  `blocked_regulated_data_present`. A blocked account is never silently
  omitted from the report; it's the headline of it. Nothing downstream of
  this module can accidentally treat a blocked account as safe — there is no
  code path that merges the two buckets.
- **Same eligibility guard as the sanctioned process**: an account is
  BLOCKED if it (or, for a rider, any ride it appears on; for a driver, the
  driver row) has any row in `rides` (as rider_id or driver_id),
  `driver_insurance_periods`, `payouts`, or `bank_accounts` — the exact four
  tables Step H checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from .. import db_supabase as db
    from ..services.rider_import_service import normalize_phone
except ImportError:  # pragma: no cover - allow direct/CLI module imports
    import db_supabase as db  # type: ignore
    from services.rider_import_service import normalize_phone  # type: ignore


@dataclass
class AccountCandidate:
    phone: str
    user_id: str
    role: str
    driver_id: str | None
    blocked: bool
    blocking_reasons: list[str] = field(default_factory=list)


@dataclass
class CleanupPlan:
    requested_phones: list[str] = field(default_factory=list)
    unmatched_phones: list[str] = field(default_factory=list)
    safe_to_delete: list[AccountCandidate] = field(default_factory=list)
    blocked_regulated_data_present: list[AccountCandidate] = field(default_factory=list)


async def _find_user_by_phone(phone: str) -> dict[str, Any] | None:
    rows = await db.get_rows("users", {"phone": phone}, limit=1, columns="id,role,phone")
    return rows[0] if rows else None


async def _find_driver_by_user_id(user_id: str) -> dict[str, Any] | None:
    rows = await db.get_rows("drivers", {"user_id": user_id}, limit=1, columns="id,user_id")
    return rows[0] if rows else None


async def _has_any_rows(table: str, filters: dict[str, Any]) -> bool:
    rows = await db.get_rows(table, filters, limit=1, columns="id")
    return bool(rows)


async def _blocking_reasons(user_id: str, driver_id: str | None) -> list[str]:
    """Same eligibility guard as purge_pii_retention() Step H: rides (as
    either party), driver_insurance_periods, payouts, bank_accounts. Checked
    independently — every reason present is reported, not just the first
    one found, so a human reviewing a blocked account sees the full picture."""
    reasons: list[str] = []

    if await _has_any_rows("rides", {"rider_id": user_id}):
        reasons.append("rides (as rider)")

    if driver_id:
        if await _has_any_rows("rides", {"driver_id": driver_id}):
            reasons.append("rides (as driver)")
        if await _has_any_rows("driver_insurance_periods", {"driver_id": driver_id}):
            reasons.append("driver_insurance_periods")
        if await _has_any_rows("payouts", {"driver_id": driver_id}):
            reasons.append("payouts")
        if await _has_any_rows("bank_accounts", {"driver_id": driver_id}):
            reasons.append("bank_accounts")

    return reasons


async def build_cleanup_plan(phones: list[str]) -> CleanupPlan:
    """Read-only end to end. Resolves each phone to a user (and, if a
    driver, their driver row), checks it against the sanctioned Step H
    eligibility guard, and buckets it. Never writes to Supabase."""
    plan = CleanupPlan(requested_phones=list(phones))

    for raw_phone in phones:
        phone = normalize_phone(raw_phone)
        user = await _find_user_by_phone(phone)
        if not user:
            plan.unmatched_phones.append(raw_phone)
            continue

        user_id = str(user["id"])
        role = user.get("role") or "unknown"
        driver_id: str | None = None
        if role == "driver":
            driver = await _find_driver_by_user_id(user_id)
            driver_id = str(driver["id"]) if driver else None

        reasons = await _blocking_reasons(user_id, driver_id)
        candidate = AccountCandidate(
            phone=phone,
            user_id=user_id,
            role=role,
            driver_id=driver_id,
            blocked=bool(reasons),
            blocking_reasons=reasons,
        )
        if candidate.blocked:
            plan.blocked_regulated_data_present.append(candidate)
        else:
            plan.safe_to_delete.append(candidate)

    return plan


def print_report(plan: CleanupPlan) -> str:
    """Human-readable dry-run report. No side effects. Blocked accounts are
    printed first and can never be mistaken for safe ones."""
    lines: list[str] = []
    w = lines.append
    w("TEST-ACCOUNT CLEANUP — DRY RUN (no writes performed, no triggers touched)")
    w("=" * 76)
    w(f"phones requested            : {len(plan.requested_phones)}")
    w(f"  -> unmatched (no account) : {len(plan.unmatched_phones)}")
    w(f"  -> blocked (regulated data present, DO NOT DELETE) : {len(plan.blocked_regulated_data_present)}")
    w(f"  -> safe to delete         : {len(plan.safe_to_delete)}")
    w("")
    if plan.blocked_regulated_data_present:
        w("BLOCKED — regulated data present, refuse to delete:")
        for c in plan.blocked_regulated_data_present:
            w(f"    user={c.user_id} phone={c.phone} role={c.role} reasons={', '.join(c.blocking_reasons)}")
        w("")
    if plan.safe_to_delete:
        w("Safe to delete (no rides/insurance-periods/payouts/bank_accounts found):")
        for c in plan.safe_to_delete:
            w(f"    user={c.user_id} phone={c.phone} role={c.role}")
        w("")
    if plan.unmatched_phones:
        w("Unmatched phones (no Spinr account found):")
        for p in plan.unmatched_phones:
            w(f"    {p}")
        w("")
    w("No DELETE was issued. No trigger was disabled. Building the actual")
    w("delete step is a separate, later, explicitly-gated change.")
    return "\n".join(lines) + "\n"
