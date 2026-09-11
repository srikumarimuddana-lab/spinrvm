"""Driver dormancy classification (owner-confirmed 2026-09-11).

**Additive-only, never deletes, never suspends, never changes go-online
eligibility.** Adds one new JSONB key to a driver's own
``legacy_import_metadata`` so admin views/KPIs can filter dormant drivers
out of active-driver reporting, without touching the row itself or any
other field on it:

- ``dormant``: ``true``
- ``dormancy_tier``: ``"dormant"`` | ``"long_dormant"``
- ``dormancy_flag``: ``{"batch", "flagged_at", "reason", "idle_days",
  "threshold_days"}`` audit record

Nothing is deleted, deactivated, or suspended. This is a reporting/
classification tool only -- see docs/change-log/2026-09-11-driver-dormancy-
flagging-tool.md for the criteria discussion this came from.

**Two dormancy tiers, both keyed off the same idle-time computation:**

- ``dormant`` (>= ``DORMANT_THRESHOLD_DAYS`` = 90 days idle): the soft,
  informational signal. Ninety days is well past any reasonable "still
  onboarding" grace period (waiting on SGI approval or a vehicle
  inspection), but short enough that a driver who comes back should not
  be surprised to find themselves excluded from active-driver counts.
- ``long_dormant`` (>= ``LONG_DORMANT_THRESHOLD_DAYS`` = 365 days idle):
  the escalation tier. Chosen to line up with Spinr's own annual
  renewal cycle (Criminal Record Check + Vulnerable Sector Check are
  renewed annually; license/insurance/inspection/background-check expiry
  are re-checked on every ``go_online`` call) -- a driver idle a full
  year needs full re-verification to come back regardless of anything
  this tool does, so naming that reality costs nothing real.

**Idle-time computation** (mirrors the ``intent_online()`` derivation
documented on ``drivers.went_online_at``/``went_offline_at``, migration 97):
the reference point is the more recent of ``went_online_at``/
``went_offline_at`` ("went dark" -- has toggled before, but not recently);
if neither is set, the reference point is ``created_at`` ("never
activated" -- created but never once tapped Go Online). A driver currently
online (``is_online = true``) is never a candidate, regardless of how old
their last toggle looks.

**Scope: only drivers whose inactivity isn't already explained by
something else tracked.** Excluded unconditionally:

- ``is_suspended = true`` or ``status`` in ``suspended``/``banned``/
  ``rejected`` -- already explained by punitive/rejection state, not
  organic dormancy.
- ``status = 'needs_review'`` -- this is the abandoned-onboarding-shell
  population ``driver_import_service.is_incomplete_onboarding_row``
  already classifies and excludes from the default admin Drivers view
  (every such row is imported with exactly this status). Flagging them
  as "dormant" too would just double-count an already-tracked bucket
  with a different label.

**Concurrent-writer guard:** same read-merge-write pattern as
``pre_launch_flag_service.apply_pre_launch_flags`` -- read the row's
current ``legacy_import_metadata`` immediately before writing, merge the
new keys in locally, and guard the ``UPDATE`` with a whole-column
optimistic-concurrency filter in addition to a narrow ``dormant IS NULL``
guard. A mismatch is reported as a conflict, never silently dropped or
retried onto a stale value.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from ..supabase_client import supabase
except ImportError:
    from supabase_client import supabase  # type: ignore

DORMANT_THRESHOLD_DAYS = 90
LONG_DORMANT_THRESHOLD_DAYS = 365

DORMANT_FLAG_KEY = "dormant"
DORMANCY_TIER_KEY = "dormancy_tier"
DORMANCY_META_KEY = "dormancy_flag"

TIER_DORMANT = "dormant"
TIER_LONG_DORMANT = "long_dormant"

REASON_NEVER_ACTIVATED = "never_activated"
REASON_WENT_DARK = "went_dark"

# Statuses whose inactivity is already explained by something else tracked
# -- see module docstring's "Scope" section. Never candidates, regardless
# of idle time.
_EXCLUDED_STATUSES = ("suspended", "banned", "rejected", "needs_review")

# Bounded worker count for the concurrent per-row apply below -- same value
# as driver_import_service._COMMIT_POOL_WORKERS / pre_launch_flag_service
# ._APPLY_POOL_WORKERS.
_APPLY_POOL_WORKERS = 20

_DRIVER_COLUMNS = "id,created_at,is_online,is_suspended,status,went_online_at,went_offline_at,legacy_import_metadata"


@dataclass
class DormancyCandidate:
    id: str
    tier: str  # "dormant" | "long_dormant"
    reason: str  # "never_activated" | "went_dark"
    idle_days: int
    legacy_import_metadata: dict[str, Any]


@dataclass
class DormancyPlan:
    candidates: list[DormancyCandidate] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def _parse_dt(value: Any) -> datetime | None:
    """Same tolerant ISO8601 parse every other service in this file's
    neighborhood uses (driver_import_service, rider_import_service, ...).
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _classify_row(row: dict[str, Any], *, now: datetime) -> tuple[str, str, int] | None:
    """Pure function: (tier, reason, idle_days) or None if not a candidate.
    Does not check is_online/status/is_suspended -- callers filter those at
    the query layer; this only computes idle time from timestamps.
    """
    went_online_at = _parse_dt(row.get("went_online_at"))
    went_offline_at = _parse_dt(row.get("went_offline_at"))

    last_activity = max((d for d in (went_online_at, went_offline_at) if d is not None), default=None)
    if last_activity is not None:
        reference, reason = last_activity, REASON_WENT_DARK
    else:
        reference, reason = _parse_dt(row.get("created_at")), REASON_NEVER_ACTIVATED

    if reference is None:
        return None

    idle_days = (now - reference).days
    if idle_days >= LONG_DORMANT_THRESHOLD_DAYS:
        return TIER_LONG_DORMANT, reason, idle_days
    if idle_days >= DORMANT_THRESHOLD_DAYS:
        return TIER_DORMANT, reason, idle_days
    return None


def _fetch_dormancy_candidate_rows() -> list[dict[str, Any]]:
    """Offline, non-punitive, fully-onboarded drivers not already flagged.
    Read-only. Idle-time filtering happens in Python (_classify_row) since
    it depends on the greater of two nullable columns, which PostgREST
    can't express directly."""
    return (
        supabase.table("drivers")
        .select(_DRIVER_COLUMNS)
        .eq("is_online", False)
        .eq("is_suspended", False)
        .not_.in_("status", list(_EXCLUDED_STATUSES))
        .filter(f"legacy_import_metadata->>{DORMANT_FLAG_KEY}", "is", "null")
        .execute()
        .data
        or []
    )


def fetch_dormancy_flagged_ids(tier: str | None = None) -> set[str]:
    """Every driver id currently flagged dormant, optionally scoped to one
    tier. Read-only.

    Shared (not duplicated) by the admin Drivers list filter so it applies
    the exact same definition of "flagged" this module's own
    apply_dormancy_flags writes.
    """
    query = supabase.table("drivers").select("id").filter(f"legacy_import_metadata->>{DORMANT_FLAG_KEY}", "eq", "true")
    if tier is not None:
        query = query.filter(f"legacy_import_metadata->>{DORMANCY_TIER_KEY}", "eq", tier)
    rows = query.execute().data or []
    return {r["id"] for r in rows if r.get("id")}


def build_dormancy_plan() -> DormancyPlan:
    """Read-only. Issues no writes."""
    plan = DormancyPlan()
    now = datetime.now(timezone.utc)
    for row in _fetch_dormancy_candidate_rows():
        classified = _classify_row(row, now=now)
        if classified is None:
            continue
        tier, reason, idle_days = classified
        plan.candidates.append(
            DormancyCandidate(
                id=row["id"],
                tier=tier,
                reason=reason,
                idle_days=idle_days,
                legacy_import_metadata=row.get("legacy_import_metadata") or {},
            )
        )
    plan.stats = {
        "dormant_candidates": sum(1 for c in plan.candidates if c.tier == TIER_DORMANT),
        "long_dormant_candidates": sum(1 for c in plan.candidates if c.tier == TIER_LONG_DORMANT),
        "never_activated": sum(1 for c in plan.candidates if c.reason == REASON_NEVER_ACTIVATED),
        "went_dark": sum(1 for c in plan.candidates if c.reason == REASON_WENT_DARK),
    }
    return plan


def _apply_flag_to_row(cand: DormancyCandidate, read_meta: dict[str, Any], *, batch: str, now_iso: str) -> str | None:
    """Read-merge-write one row's legacy_import_metadata, guarded by a
    narrow key check plus a whole-column optimistic-concurrency filter (see
    module docstring for why the second guard exists). Returns the driver
    id if the guard didn't match (conflict), else None.
    """
    meta = dict(read_meta)
    meta[DORMANT_FLAG_KEY] = True
    meta[DORMANCY_TIER_KEY] = cand.tier
    meta[DORMANCY_META_KEY] = {
        "batch": batch,
        "flagged_at": now_iso,
        "reason": cand.reason,
        "idle_days": cand.idle_days,
        "threshold_days": LONG_DORMANT_THRESHOLD_DAYS if cand.tier == TIER_LONG_DORMANT else DORMANT_THRESHOLD_DAYS,
    }

    res = (
        supabase.table("drivers")
        .update({"legacy_import_metadata": meta, "updated_at": now_iso})
        .eq("id", cand.id)
        .filter(f"legacy_import_metadata->>{DORMANT_FLAG_KEY}", "is", "null")
        .filter("legacy_import_metadata", "eq", json.dumps(read_meta, sort_keys=True, default=str))
        .execute()
    )
    return cand.id if not res.data else None


def apply_dormancy_flags(plan: DormancyPlan, *, batch: str) -> list[str]:
    """Flag every candidate in plan.candidates.

    Re-reads each row's current legacy_import_metadata immediately before
    writing (not the plan-time snapshot) -- the plan step only proves the
    flag was absent *when planned*; a concurrent writer between plan and
    apply is caught by the guard in _apply_flag_to_row, never silently
    overwritten. Safe to re-run: a re-plan after a partial apply only ever
    contains rows still missing the flag.

    Returns the list of conflicted driver ids -- empty on a clean run.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    def _apply_one(cand: DormancyCandidate) -> str | None:
        existing = supabase.table("drivers").select("legacy_import_metadata").eq("id", cand.id).execute().data
        read_meta = dict((existing[0].get("legacy_import_metadata") or {}) if existing else {})
        if DORMANT_FLAG_KEY in read_meta:
            return None  # already flagged by someone else -- not a conflict, just a no-op
        return _apply_flag_to_row(cand, read_meta, batch=batch, now_iso=now_iso)

    with ThreadPoolExecutor(max_workers=_APPLY_POOL_WORKERS, thread_name_prefix="dormancy-flag-apply") as pool:
        results = [fut.result() for fut in [pool.submit(_apply_one, c) for c in plan.candidates]]

    return [conflict_id for conflict_id in results if conflict_id]


def print_report(plan: DormancyPlan, *, dry_run: bool) -> None:
    """Counts only -- never PII (no name/phone/address)."""
    mode = "DRY RUN" if dry_run else "COMMIT"
    print(f"\n=== Driver dormancy flagging ({mode}) ===")
    print(f"  dormant (>= {DORMANT_THRESHOLD_DAYS}d idle)        : {plan.stats.get('dormant_candidates', 0)}")
    print(
        f"  long_dormant (>= {LONG_DORMANT_THRESHOLD_DAYS}d idle)     : {plan.stats.get('long_dormant_candidates', 0)}"
    )
    print(f"    never activated (no went_online_at ever) : {plan.stats.get('never_activated', 0)}")
    print(f"    went dark (toggled before, not recently) : {plan.stats.get('went_dark', 0)}")
    print("\n  Additive only -- sets legacy_import_metadata.dormant = true.")
    print("  No row is deleted, deactivated, suspended, or otherwise mutated.\n")
