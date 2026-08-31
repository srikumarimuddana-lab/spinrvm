"""Pre-launch legacy data flagging (owner-confirmed 2026-08-30: Spinr's
public launch was 2026-03-30).

**Additive-only, never deletes.** Adds two new JSONB keys to
``legacy_import_metadata`` on already-migrated ``drivers``/``rides`` rows so
admin views/KPIs can filter pre-launch test data out, without touching the
row itself:

- ``pre_launch_test``: ``true``
- ``pre_launch_flag``: ``{"batch", "flagged_at", "reason"}`` audit record

Nothing is deleted or deactivated. This is a decision made explicitly, not a
default: see docs/change-log/2026-08-30-pre-launch-flag-tool.md for the full
reasoning and the AskUserQuestion exchange it came from.

**Scope, and why it's activity-gated rather than date-gated:**

- **Drivers**: a driver's own ``created_at`` predating launch does NOT by
  itself mean the profile is test data — 33 of the original 343
  source-tagged, pre-launch-``created_at`` drivers had driven a real ride or
  had a real ``driver_insurance_periods`` row, meaning they onboarded during
  a pre-launch beta/soft-launch window and are real, active drivers. The
  criterion is therefore activity-based, not date-based: legacy-imported AND
  **zero** rides ever driven AND **zero** ``driver_insurance_periods`` rows
  ever — i.e. a dormant profile with no real footprint at all, regardless of
  when its ``created_at`` falls.
- **Broadened 2026-08-31 (owner decision)**, dropping two restrictions the
  original version had:
  - *"Legacy-imported" no longer requires a top-level ``source`` key.* It
    also matches a ``mongo_driver_history`` key — the shape
    ``driver_import_service.py``'s link/enrich split writes on a driver row
    it linked to an existing rider account or enriched onto an existing
    driver, rather than net-new-inserting (see that module's "existing-match
    link/enrich policy"). Confirmed against production this is the complete
    partition: of 910 total driver rows, 697+187 carry a top-level
    ``source``, 25 carry only ``mongo_driver_history``, and exactly 1 (the
    lone organic signup) carries neither — no third shape exists.
  - *The ``created_at < LAUNCH_DATE`` gate is dropped entirely.* Many
    legacy rows carry their *original* old-app signup date (the importer
    preserves it rather than stamping import time), and the old app kept
    accepting real signups well past Spinr's 2026-03-30 launch — it wasn't
    decommissioned until Oct 31. Gating on that date left 272 of the 487
    "Unnamed Legacy Driver" placeholder rows (themselves already confirmed
    to have zero ride linkage — see the blank-name root-cause doc) sitting
    unflagged and fully visible in the default admin Drivers list, for no
    reason connected to whether they're real activity.
  - The zero-activity guard itself is **unchanged and still unconditional**
    — broadening what counts as "legacy-imported" or dropping the date gate
    must never widen who the activity guard excludes. A driver with a real
    ride or a real insurance-period row is never a candidate, full stop,
    regardless of source shape or created_at.
- **Rides**: no comparable ambiguity — a ride either happened or it didn't,
  and there is no real customer base to serve before launch by definition.
  Every ride with ``created_at`` before 2026-03-30 is flagged (25 in the
  reference dataset, all status ``completed`` — real pre-launch test trips,
  not failed/cancelled noise).

Money-adjacent note: this module does NOT touch ``wallets``/
``wallet_transactions`` — the pre-launch wallet exclusion already lives in
``wallet_import_service.py``'s ``LAUNCH_DATE`` skip (that importer had never
been run, so there was nothing already-migrated to flag there).

**Concurrent-writer guard, ``rides`` table:** ``rides.legacy_import_metadata``
already has two other read-merge-write backfills touching it
(``legacy_gst_backfill_service.py``, ``booking_import_service
.apply_duration_estimated_backfill``) — see the banner comment in
``legacy_gst_backfill_service.py`` for the full reasoning. This module's
``apply_pre_launch_flags`` uses the exact same whole-column
optimistic-concurrency guard ``apply_duration_estimated_backfill`` does:
read the row's current ``legacy_import_metadata`` immediately before
writing, merge the new keys in locally, and guard the ``UPDATE`` with
``.filter("legacy_import_metadata", "eq", <json of what was just read>)`` in
addition to a narrow ``pre_launch_test IS NULL`` guard. A mismatch (0 rows
updated) is reported as a conflict, never silently dropped or retried onto a
stale value — the same row is picked up cleanly on the next plan/apply
cycle. Applied to ``drivers`` too, for the same reason, even though no other
backfill is known to write ``drivers.legacy_import_metadata`` concurrently
today — strictly safer, and cheap to include.
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

# Spinr's public launch date (owner-confirmed 2026-08-30). Mirrors
# wallet_import_service.LAUNCH_DATE -- duplicated per this repo's per-module
# small-constant convention rather than cross-imported, since the two
# modules operate on entirely different tables.
LAUNCH_DATE = "2026-03-30"

PRE_LAUNCH_FLAG_KEY = "pre_launch_test"
PRE_LAUNCH_META_KEY = "pre_launch_flag"

# Bounded worker count for the concurrent per-row apply below -- same value
# as driver_import_service._COMMIT_POOL_WORKERS /
# booking_import_service._APPLY_POOL_WORKERS.
_APPLY_POOL_WORKERS = 20

FLAG_REASON = "created before Spinr's 2026-03-30 public launch; owner-confirmed pre-launch test data"


@dataclass
class FlagCandidate:
    table: str  # "drivers" | "rides"
    id: str
    legacy_import_metadata: dict[str, Any]


@dataclass
class PreLaunchFlagPlan:
    driver_candidates: list[FlagCandidate] = field(default_factory=list)
    ride_candidates: list[FlagCandidate] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def _select_in(table: str, columns: str, column: str, values: list[str], chunk: int = 200) -> list[dict[str, Any]]:
    """Batched SELECT ... WHERE column IN (values). Duplicated per this
    repo's existing per-module small-helper convention."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(values), chunk):
        batch = values[i : i + chunk]
        if not batch:
            continue
        rows = supabase.table(table).select(columns).in_(column, batch).execute().data or []
        out.extend(rows)
    return out


# Both top-level shapes a legacy-imported driver row can carry -- see the
# module docstring's "Broadened 2026-08-31" note. Checked as two separate
# queries and unioned by id rather than one PostgREST `.or_()` call: this
# matches the codebase's existing "resolve ids from N queries, then combine"
# convention (e.g. admin_get_drivers's photo_status filter) and keeps each
# query a plain single-key JSONB-path filter, which is what the shared test
# fake and every other caller of this pattern already expects.
_LEGACY_IMPORT_MARKER_KEYS = ("source", "mongo_driver_history")


def _fetch_drivers_with_metadata_key(key: str) -> list[dict[str, Any]]:
    """Drivers carrying a non-null legacy_import_metadata->>key, not already
    pre-launch-flagged. Read-only."""
    return (
        supabase.table("drivers")
        .select("id,legacy_import_metadata")
        .filter(f"legacy_import_metadata->>{key}", "not.is", "null")
        .filter("legacy_import_metadata->>pre_launch_test", "is", "null")
        .execute()
        .data
        or []
    )


def _fetch_pre_launch_driver_candidates() -> list[FlagCandidate]:
    """Legacy-imported drivers, not already flagged, with zero rides ever
    driven and zero driver_insurance_periods rows. Read-only.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for key in _LEGACY_IMPORT_MARKER_KEYS:
        for row in _fetch_drivers_with_metadata_key(key):
            if row.get("id"):
                by_id[row["id"]] = row
    legacy_pre_launch = list(by_id.values())
    if not legacy_pre_launch:
        return []

    driver_ids = [r["id"] for r in legacy_pre_launch if r.get("id")]

    drivers_with_rides = {
        r["driver_id"] for r in _select_in("rides", "driver_id", "driver_id", driver_ids) if r.get("driver_id")
    }
    drivers_with_insurance = {
        r["driver_id"]
        for r in _select_in("driver_insurance_periods", "driver_id", "driver_id", driver_ids)
        if r.get("driver_id")
    }
    has_activity = drivers_with_rides | drivers_with_insurance

    return [
        FlagCandidate(table="drivers", id=r["id"], legacy_import_metadata=r.get("legacy_import_metadata") or {})
        for r in legacy_pre_launch
        if r["id"] not in has_activity
    ]


def _fetch_pre_launch_ride_candidates() -> list[FlagCandidate]:
    """Every ride created before launch, not already flagged. Read-only.
    Unlike drivers, no activity-based exclusion -- a ride either happened or
    it didn't, and there is no real customer base to serve before launch.
    """
    rows = (
        supabase.table("rides")
        .select("id,legacy_import_metadata")
        .filter("legacy_import_metadata->>pre_launch_test", "is", "null")
        .lt("created_at", LAUNCH_DATE)
        .execute()
        .data
        or []
    )
    return [
        FlagCandidate(table="rides", id=r["id"], legacy_import_metadata=r.get("legacy_import_metadata") or {})
        for r in rows
    ]


def fetch_pre_launch_flagged_ids(table: str) -> set[str]:
    """Every id in ``table`` currently flagged ``pre_launch_test = true``.
    Read-only.

    Shared (not duplicated) by the admin drivers/rides list filters
    (``routes/admin/drivers.py``, ``routes/admin/rides.py``) so both apply
    the exact same definition of "flagged" this module's own
    ``apply_pre_launch_flags`` writes -- a hand-duplicated copy of the
    ``PRE_LAUNCH_FLAG_KEY`` JSONB-path filter in each route risks drifting
    from the writer's own definition.
    """
    rows = (
        supabase.table(table)
        .select("id")
        .filter(f"legacy_import_metadata->>{PRE_LAUNCH_FLAG_KEY}", "eq", "true")
        .execute()
        .data
        or []
    )
    return {r["id"] for r in rows if r.get("id")}


def build_pre_launch_flag_plan() -> PreLaunchFlagPlan:
    """Read-only. Issues no writes."""
    plan = PreLaunchFlagPlan()
    plan.driver_candidates = _fetch_pre_launch_driver_candidates()
    plan.ride_candidates = _fetch_pre_launch_ride_candidates()
    plan.stats = {
        "driver_candidates": len(plan.driver_candidates),
        "ride_candidates": len(plan.ride_candidates),
    }
    return plan


def _apply_flag_to_row(table: str, row_id: str, read_meta: dict[str, Any], *, batch: str, now_iso: str) -> str | None:
    """Read-merge-write one row's legacy_import_metadata, guarded by a
    narrow key check plus a whole-column optimistic-concurrency filter (see
    module docstring for why the second guard exists). Returns row_id if the
    guard didn't match (conflict -- already flagged, or some other writer
    touched this row's metadata since the plan was built), else None.
    """
    meta = dict(read_meta)
    meta[PRE_LAUNCH_FLAG_KEY] = True
    meta[PRE_LAUNCH_META_KEY] = {"batch": batch, "flagged_at": now_iso, "reason": FLAG_REASON}

    res = (
        supabase.table(table)
        .update({"legacy_import_metadata": meta, "updated_at": now_iso})
        .eq("id", row_id)
        .filter(f"legacy_import_metadata->>{PRE_LAUNCH_FLAG_KEY}", "is", "null")
        .filter("legacy_import_metadata", "eq", json.dumps(read_meta, sort_keys=True, default=str))
        .execute()
    )
    return row_id if not res.data else None


def apply_pre_launch_flags(plan: PreLaunchFlagPlan, *, batch: str) -> dict[str, list[str]]:
    """Flag every candidate in plan.driver_candidates/ride_candidates.

    Re-reads each row's current legacy_import_metadata immediately before
    writing (not the plan-time snapshot) -- the plan step only proves the
    flag was absent *when planned*; a concurrent writer between plan and
    apply is caught by the guard in _apply_flag_to_row, never silently
    overwritten. Safe to re-run: a re-plan after a partial apply only ever
    contains rows still missing the flag.

    Returns {"drivers": [conflicted ids], "rides": [conflicted ids]} --
    empty lists on a clean run.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    def _apply_one(cand: FlagCandidate) -> tuple[str, str | None]:
        # Re-read immediately before writing -- the plan snapshot could be
        # stale by the time this runs (see docstring above).
        existing = supabase.table(cand.table).select("legacy_import_metadata").eq("id", cand.id).execute().data
        read_meta = dict((existing[0].get("legacy_import_metadata") or {}) if existing else {})
        if PRE_LAUNCH_FLAG_KEY in read_meta:
            return cand.table, None  # already flagged by someone else -- not a conflict, just a no-op
        conflict = _apply_flag_to_row(cand.table, cand.id, read_meta, batch=batch, now_iso=now_iso)
        return cand.table, conflict

    all_candidates = plan.driver_candidates + plan.ride_candidates
    with ThreadPoolExecutor(max_workers=_APPLY_POOL_WORKERS, thread_name_prefix="pre-launch-flag-apply") as pool:
        results = [fut.result() for fut in [pool.submit(_apply_one, c) for c in all_candidates]]

    conflicts: dict[str, list[str]] = {"drivers": [], "rides": []}
    for table, conflict_id in results:
        if conflict_id:
            conflicts[table].append(conflict_id)
    return conflicts


def print_report(plan: PreLaunchFlagPlan, *, dry_run: bool) -> None:
    """Counts and ids only -- never PII (no name/phone/address)."""
    mode = "DRY RUN" if dry_run else "COMMIT"
    print(f"\n=== Pre-launch legacy data flagging ({mode}) ===")
    print(f"  driver candidates (dormant, pre-launch)  : {plan.stats.get('driver_candidates', 0)}")
    print(f"  ride candidates (all pre-launch)         : {plan.stats.get('ride_candidates', 0)}")
    print("\n  Additive only -- sets legacy_import_metadata.pre_launch_test = true.")
    print("  No row is deleted, deactivated, or otherwise mutated.\n")
