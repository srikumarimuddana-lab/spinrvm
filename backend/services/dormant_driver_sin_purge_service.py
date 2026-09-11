"""Dormant-driver SIN purge (ACTION_ITEMS.md A34).

Scope decided by the product owner, 2026-09-11, in response to
``docs/change-log/2026-09-10-a34-pre-launch-data-contamination-check.md``'s
finding that 97 of the 854 confirmed-dormant (zero real activity, ever)
legacy-imported driver profiles still carry a real SIN:

- **SIN only, for now.** ``date_of_birth`` (146 profiles),
  ``driver_vehicle_history`` (241 profiles), and dormant riders'
  ``saved_addresses`` (153 profiles) are explicitly NOT touched by this
  module -- each is its own separate purge decision, not yet made.
- **180-day grace period past Spinr's 2026-03-30 public launch** (cutoff
  2026-09-26), not "dormant since import." A profile that imported cleanly
  but simply hasn't taken its first ride yet is not the same as one that
  never will -- stripping its SIN prematurely would just make a real future
  driver re-submit a document they already gave once. The grace period is
  enforced in code (``apply_sin_purge`` refuses to write before the
  cutoff), not just documented as a convention to follow.
- **Population is the pre_launch_flag_service.py-flagged set**, not
  re-derived here. That module already establishes the shared, admin-visible
  definition of "confirmed dormant" (legacy-imported AND zero rides ever AND
  zero driver_insurance_periods rows); this module trusts that flag rather
  than recomputing the activity gate a second time.

**This deletes the actual vault.secrets ciphertext, not just the column
reference.** Migration 289's own top comment already warns that nulling
``drivers.sin`` alone orphans the vault row without deleting it -- "the
ciphertext is unreachable but retained, which is a PIPEDA problem, not a
clean [purge]." Migration 413 (``purge_driver_pii_secret`` RPC) closes that
gap; this module calls it before nulling the column, in that order, matching
289's own note ("run that BEFORE dropping the column").

Never logs, prints, or returns a raw SIN value -- only ids and counts, same
discipline as ``pre_launch_flag_service.print_report``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

try:
    from ..supabase_client import supabase
    from .pre_launch_flag_service import PRE_LAUNCH_FLAG_KEY
except ImportError:
    from services.pre_launch_flag_service import PRE_LAUNCH_FLAG_KEY  # type: ignore
    from supabase_client import supabase  # type: ignore

# Spinr's public launch date -- duplicated from pre_launch_flag_service.LAUNCH_DATE
# per this repo's existing per-module small-constant convention (see that
# module's own comment on why it duplicates wallet_import_service.LAUNCH_DATE
# rather than cross-importing).
_LAUNCH_DATE = "2026-03-30"

# Product-owner decision, 2026-09-11 (ACTION_ITEMS.md A34, "180 days"
# AskUserQuestion answer) -- see module docstring for the reasoning.
GRACE_PERIOD_DAYS = 180

PURGE_META_KEY = "dormant_sin_purge"
PURGE_REASON = "dormant driver, >180d post-launch with zero activity ever; product-owner-approved 2026-09-11"

_APPLY_POOL_WORKERS = 20


def grace_period_cutoff() -> date:
    """The date on/after which a dormant driver's SIN becomes purge-eligible."""
    launch = datetime.strptime(_LAUNCH_DATE, "%Y-%m-%d").date()
    return launch + timedelta(days=GRACE_PERIOD_DAYS)


def is_grace_period_elapsed(*, today: date | None = None) -> bool:
    today = today if today is not None else datetime.now(timezone.utc).date()
    return today >= grace_period_cutoff()


@dataclass
class SinPurgeCandidate:
    id: str
    sin: str
    legacy_import_metadata: dict[str, Any]


@dataclass
class SinPurgePlan:
    candidates: list[SinPurgeCandidate] = field(default_factory=list)
    grace_period_elapsed: bool = False
    grace_period_cutoff: str = ""


def build_sin_purge_plan(*, today: date | None = None) -> SinPurgePlan:
    """Read-only. Issues no writes.

    Reports the full candidate list regardless of whether the grace period
    has elapsed, so a caller (the CLI script, or an admin report) can see
    what WOULD be purged and when -- ``apply_sin_purge`` is what actually
    refuses to write before the cutoff, not this function.
    """
    plan = SinPurgePlan()
    plan.grace_period_elapsed = is_grace_period_elapsed(today=today)
    plan.grace_period_cutoff = grace_period_cutoff().isoformat()

    rows = (
        supabase.table("drivers")
        .select("id,sin,legacy_import_metadata")
        .filter(f"legacy_import_metadata->>{PRE_LAUNCH_FLAG_KEY}", "eq", "true")
        .filter("sin", "not.is", "null")
        .execute()
        .data
        or []
    )
    plan.candidates = [
        SinPurgeCandidate(id=r["id"], sin=r["sin"], legacy_import_metadata=r.get("legacy_import_metadata") or {})
        for r in rows
        if r.get("id") and r.get("sin")
    ]
    return plan


def purge_pii_secret(secret_id: str | None) -> bool:
    """Delete a driver-PII vault.secrets row via migration 413's RPC.
    Returns whether a row was actually deleted."""
    if not secret_id:
        return False
    res = supabase.rpc("purge_driver_pii_secret", {"secret_id": secret_id}).execute()
    return bool(getattr(res, "data", False))


def apply_sin_purge(plan: SinPurgePlan, *, batch: str, today: date | None = None) -> dict[str, list[str]]:
    """Delete the vault.secrets ciphertext for every candidate's sin (in that
    order -- see module docstring), then null sin/sin_last4/sin_collected_at
    on the driver row.

    Refuses entirely if the grace period hasn't elapsed -- a hard, code-level
    gate, not just a documented convention, since firing early means an
    irreversible loss of a real government ID a driver may still need.

    Each row is re-read immediately before writing and the UPDATE is guarded
    on the sin value the plan actually saw (same optimistic-concurrency
    pattern as pre_launch_flag_service.apply_pre_launch_flags): a driver
    whose sin changed between plan and apply (self-entry, or another apply
    run already purged it) is reported as a conflict, never silently
    overwritten or re-purged. Safe to re-run: a driver whose sin is already
    null is not re-selected by build_sin_purge_plan.

    Returns {"purged": [ids], "conflicts": [ids]}.
    """
    if not is_grace_period_elapsed(today=today):
        raise RuntimeError(
            f"refusing to apply: grace period has not elapsed (eligible on {grace_period_cutoff().isoformat()})"
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    def _apply_one(cand: SinPurgeCandidate) -> tuple[str, bool]:
        existing = supabase.table("drivers").select("sin,legacy_import_metadata").eq("id", cand.id).execute().data
        current_sin = existing[0].get("sin") if existing else None
        if current_sin != cand.sin:
            return cand.id, False  # conflict -- changed since plan, not touched

        purge_pii_secret(current_sin)

        meta = dict((existing[0].get("legacy_import_metadata") or {}) if existing else {})
        meta[PURGE_META_KEY] = {"batch": batch, "purged_at": now_iso, "reason": PURGE_REASON}

        res = (
            supabase.table("drivers")
            .update(
                {
                    "sin": None,
                    "sin_last4": None,
                    "sin_collected_at": None,
                    "legacy_import_metadata": meta,
                    "updated_at": now_iso,
                }
            )
            .eq("id", cand.id)
            .eq("sin", cand.sin)
            .execute()
        )
        return cand.id, bool(res.data)

    if not plan.candidates:
        return {"purged": [], "conflicts": []}

    with ThreadPoolExecutor(max_workers=_APPLY_POOL_WORKERS, thread_name_prefix="sin-purge-apply") as pool:
        results = [fut.result() for fut in [pool.submit(_apply_one, c) for c in plan.candidates]]

    return {
        "purged": [i for i, ok in results if ok],
        "conflicts": [i for i, ok in results if not ok],
    }


def print_report(plan: SinPurgePlan, *, dry_run: bool) -> None:
    """Counts and ids only -- never a raw SIN value."""
    mode = "DRY RUN" if dry_run else "COMMIT"
    print(f"\n=== Dormant driver SIN purge ({mode}) ===")
    print(f"  candidates (dormant, SIN on file)  : {len(plan.candidates)}")
    print(f"  grace period cutoff                : {plan.grace_period_cutoff}")
    print(f"  grace period elapsed                : {plan.grace_period_elapsed}")
    if not plan.grace_period_elapsed:
        print("\n  Not yet eligible -- apply will refuse until the cutoff date above.\n")
    else:
        print("\n  Deletes the vault.secrets ciphertext, then nulls sin/sin_last4/")
        print("  sin_collected_at. No other column or table is touched.\n")
