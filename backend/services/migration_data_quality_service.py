"""Migration data-quality scan (owner-confirmed 2026-08-31 investigation,
docs/runbooks/migration-data-quality-strategy.md).

**Additive-only, never deletes, never touches ``rides.status``.** Adds one
new JSONB key to ``legacy_import_metadata`` on already-migrated ``rides``
rows so admin views can filter the four import-quality anomaly categories
found in production, without reclassifying the ride's state-machine status
(a `completed` legacy row may already have GST/T4A/insurance-period figures
computed against it -- mutating `status` to signal a data-quality
observation would be a destructive edit to an already-audited financial
record; see the runbook's ``§2``):

- ``missing_driver``  -- completed ride, ``driver_id`` is null. Booking
  import matched the rider by phone but not the driver (see
  ``booking_import_service._match_rider_driver``): the driver's phone in the
  source booking never matched anyone already in ``drivers`` at import time.
- ``missing_rider``   -- completed ride, ``rider_id`` is null. Same
  mechanism, rider side.
- ``placeholder_address`` -- ``pickup_address`` or ``dropoff_address`` is
  the literal ``"Address unavailable (imported ride)"`` string
  ``booking_import_service`` substitutes when the source address was blank.
- ``zero_fare`` -- completed ride, ``grand_total`` is null or 0. The old
  app allowed free/comped/test bookings; the number is accurate to the
  source, not a computation bug -- see the runbook's ``§1.B``.

A ride can carry more than one issue at once (e.g. missing driver AND
zero fare) -- ``data_quality`` is a list of issue strings, not a single
value, so a real multi-issue row isn't forced to pick one.

Mirrors ``pre_launch_flag_service.py``'s build_plan / apply / fetch_flagged
three-function shape and its read-merge-write optimistic-concurrency guard
on ``legacy_import_metadata`` (same rationale: this column already has
multiple read-merge-write backfills touching it -- see that module's
"Concurrent-writer guard" docstring section for the full reasoning, which
applies unchanged here).
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

DATA_QUALITY_KEY = "data_quality"
DATA_QUALITY_META_KEY = "data_quality_flag"

PLACEHOLDER_ADDRESS = "Address unavailable (imported ride)"

# Bounded worker count for the concurrent per-row apply below -- same value
# as pre_launch_flag_service._APPLY_POOL_WORKERS / driver_import_service
# ._COMMIT_POOL_WORKERS / booking_import_service._APPLY_POOL_WORKERS.
_APPLY_POOL_WORKERS = 20

ISSUE_MISSING_DRIVER = "missing_driver"
ISSUE_MISSING_RIDER = "missing_rider"
ISSUE_PLACEHOLDER_ADDRESS = "placeholder_address"
ISSUE_ZERO_FARE = "zero_fare"

ALL_ISSUES = (ISSUE_MISSING_DRIVER, ISSUE_MISSING_RIDER, ISSUE_PLACEHOLDER_ADDRESS, ISSUE_ZERO_FARE)


@dataclass
class ScanCandidate:
    id: str
    issues: list[str]
    legacy_import_metadata: dict[str, Any]


@dataclass
class DataQualityScanPlan:
    candidates: list[ScanCandidate] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _rows_missing_driver() -> list[dict[str, Any]]:
    """Completed rides with a null/empty driver_id, not already flagged for
    this issue. Read-only."""
    return (
        supabase.table("rides")
        .select("id,legacy_import_metadata")
        .eq("status", "completed")
        .is_("driver_id", "null")
        .execute()
        .data
        or []
    )


def _rows_missing_rider() -> list[dict[str, Any]]:
    """Completed rides with a null/empty rider_id. Read-only."""
    return (
        supabase.table("rides")
        .select("id,legacy_import_metadata")
        .eq("status", "completed")
        .is_("rider_id", "null")
        .execute()
        .data
        or []
    )


def _rows_placeholder_address() -> list[dict[str, Any]]:
    """Rides whose pickup or dropoff address is the literal import-time
    placeholder string. Read-only. Two queries, unioned by id below --
    PostgREST can't OR two different columns' equality in one call without
    the shared `$or` filter DSL this module deliberately doesn't take a
    dependency on (see pre_launch_flag_service's precedent for the same
    "resolve by id, then union" choice)."""
    pickup = (
        supabase.table("rides")
        .select("id,legacy_import_metadata")
        .eq("pickup_address", PLACEHOLDER_ADDRESS)
        .execute()
        .data
        or []
    )
    dropoff = (
        supabase.table("rides")
        .select("id,legacy_import_metadata")
        .eq("dropoff_address", PLACEHOLDER_ADDRESS)
        .execute()
        .data
        or []
    )
    by_id: dict[str, dict[str, Any]] = {}
    for row in pickup + dropoff:
        if row.get("id"):
            by_id[row["id"]] = row
    return list(by_id.values())


def _rows_zero_fare() -> list[dict[str, Any]]:
    """Completed rides with a null or zero grand_total. Read-only."""
    rows = (
        supabase.table("rides").select("id,legacy_import_metadata,grand_total").eq("status", "completed").execute().data
        or []
    )
    return [r for r in rows if not r.get("grand_total")]


def build_data_quality_scan_plan() -> DataQualityScanPlan:
    """Read-only. Issues no writes.

    Runs all four category queries, merges results by ride id (a ride
    already carrying data_quality.<issue> for a given issue is excluded
    from that issue re-appearing -- re-running the scan after a partial
    apply only surfaces genuinely new/unflagged issues), and returns one
    candidate per affected ride with the full list of issues it has.
    """
    by_id: dict[str, ScanCandidate] = {}

    def _merge(rows: list[dict[str, Any]], issue: str) -> None:
        for row in rows:
            row_id = row.get("id")
            if not row_id:
                continue
            meta = row.get("legacy_import_metadata") or {}
            existing_issues = set((meta.get(DATA_QUALITY_KEY) or {}).get("issues") or [])
            if issue in existing_issues:
                continue
            cand = by_id.get(row_id)
            if cand is None:
                cand = ScanCandidate(id=row_id, issues=[], legacy_import_metadata=meta)
                by_id[row_id] = cand
            if issue not in cand.issues:
                cand.issues.append(issue)

    _merge(_rows_missing_driver(), ISSUE_MISSING_DRIVER)
    _merge(_rows_missing_rider(), ISSUE_MISSING_RIDER)
    _merge(_rows_placeholder_address(), ISSUE_PLACEHOLDER_ADDRESS)
    _merge(_rows_zero_fare(), ISSUE_ZERO_FARE)

    plan = DataQualityScanPlan(candidates=list(by_id.values()))
    stats: dict[str, int] = {issue: 0 for issue in ALL_ISSUES}
    for cand in plan.candidates:
        for issue in cand.issues:
            stats[issue] = stats.get(issue, 0) + 1
    stats["rides_affected"] = len(plan.candidates)
    plan.stats = stats
    return plan


def fetch_needs_review_ride_ids() -> set[str]:
    """Every ride id currently carrying a non-empty data_quality.issues
    list. Read-only.

    Shared by the admin rides list filter (routes/admin/rides.py's
    `status=needs_review` synthetic value) so it applies the exact same
    definition of "flagged" this module's own apply_data_quality_flags
    writes -- same sharing pattern as pre_launch_flag_service
    .fetch_pre_launch_flagged_ids.
    """
    rows = (
        supabase.table("rides")
        .select("id")
        .filter(f"legacy_import_metadata->{DATA_QUALITY_KEY}->issues", "not.is", "null")
        .execute()
        .data
        or []
    )
    return {r["id"] for r in rows if r.get("id")}


def _apply_flag_to_row(
    row_id: str, read_meta: dict[str, Any], issues: list[str], *, batch: str, now_iso: str
) -> str | None:
    """Read-merge-write one row's legacy_import_metadata, guarded by a
    whole-column optimistic-concurrency filter (see module docstring).
    Returns row_id if the guard didn't match (conflict), else None.
    """
    existing_issues = set((read_meta.get(DATA_QUALITY_KEY) or {}).get("issues") or [])
    merged_issues = sorted(existing_issues | set(issues))

    meta = dict(read_meta)
    meta[DATA_QUALITY_KEY] = {
        "issues": merged_issues,
        DATA_QUALITY_META_KEY: {"batch": batch, "detected_at": now_iso, "detected_by": "migration_data_quality_scan"},
    }

    res = (
        supabase.table("rides")
        .update({"legacy_import_metadata": meta, "updated_at": now_iso})
        .eq("id", row_id)
        .filter("legacy_import_metadata", "eq", json.dumps(read_meta, sort_keys=True, default=str))
        .execute()
    )
    return row_id if not res.data else None


def apply_data_quality_flags(plan: DataQualityScanPlan, *, batch: str) -> list[str]:
    """Flag every candidate in plan.candidates.

    Re-reads each row's current legacy_import_metadata immediately before
    writing (not the plan-time snapshot) -- same pattern and rationale as
    pre_launch_flag_service.apply_pre_launch_flags. Safe to re-run: a
    re-plan after a partial apply only ever contains rows still missing at
    least one of their planned issues.

    Returns the list of conflicted ride ids (empty on a clean run).
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    def _apply_one(cand: ScanCandidate) -> str | None:
        existing = supabase.table("rides").select("legacy_import_metadata").eq("id", cand.id).execute().data
        read_meta = dict((existing[0].get("legacy_import_metadata") or {}) if existing else {})
        existing_issues = set((read_meta.get(DATA_QUALITY_KEY) or {}).get("issues") or [])
        if existing_issues >= set(cand.issues):
            return None  # already flagged for everything this candidate needs -- not a conflict, just a no-op
        return _apply_flag_to_row(cand.id, read_meta, cand.issues, batch=batch, now_iso=now_iso)

    with ThreadPoolExecutor(max_workers=_APPLY_POOL_WORKERS, thread_name_prefix="data-quality-flag-apply") as pool:
        results = [fut.result() for fut in [pool.submit(_apply_one, c) for c in plan.candidates]]

    return [row_id for row_id in results if row_id]
