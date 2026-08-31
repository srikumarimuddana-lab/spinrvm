"""Driver-repair pass for legacy-imported completed rides missing a driver
link (owner follow-up 2026-08-31 to migration_data_quality_service.py's
``missing_driver`` finding: "the 'repair pass' that re-checks unmatched
driver/rider phones against current data").

**Scope is driver-side only.** A ride's ``legacy_import_metadata`` carries
the source booking's ``old_driver_id`` (see booking_import_service.py), so a
current-data re-match is possible: a driver row carrying that old id may
exist now that didn't exist at the ride's own import time (added in a later
driver-import batch). There is no equivalent for the rider side -- no
``users`` row anywhere stores an old-system customer-id linkage (the
``legacy_id_crosswalk`` table built for exactly this, migration 328, is
still empty/unbackfilled -- ACTION_ITEMS.md A34), so a rider-side repair
pass cannot be built from Supabase data alone. It needs either the raw
``customers.csv`` Mongo export or that crosswalk populated first.

**This is NOT a metadata-only tool like migration_data_quality_service.py --
it mutates ``rides.driver_id``, a real field with two real downstream
effects that must be repaired alongside it, or the ride is left in a worse
state than before:**

1. ``driver_insurance_periods`` (regulatory, 7-year audit retention, append
   -only) -- a ``missing_driver`` ride never got its Period 2/3 rows at
   import time (``booking_import_service._plan_insurance_periods`` returns
   immediately when ``driver_id`` is falsy). Backfilling driver_id without
   also reconstructing those periods would leave a driver now linked to a
   completed trip with no insurance-period audit trail for it. This module
   reconstructs them the same way booking_import_service does at import
   time and migration 332 did historically: Period 2 arrived_at->started_at,
   Period 3 started_at->completed_at, ``is_reconstructed=True``, only when
   every timestamp a period needs is present.
2. ``payable_balance`` (money -- routes/drivers/earnings.py derives it LIVE
   as sum of completed-ride ``driver_earnings`` plus bonuses minus payouts).
   The ride's ``driver_earnings`` was already computed and stored at import
   time regardless of whether a driver matched; simply pointing driver_id at
   it now would silently increase that driver's live payable balance by
   real dollars for a trip already settled in the old app. Neutralized the
   same way the original import neutralizes every matched driver's earnings:
   one offsetting ``payouts`` row (status='completed') per driver, sized to
   exactly cancel the newly-linked earnings. Reuses
   ``booking_import_service.payout_id_for``/``recount_drivers`` rather than
   reimplementing the idempotency-key scheme or the total_rides recompute.

Mirrors migration_data_quality_service.py's/pre_launch_flag_service.py's
build_plan / apply two-function shape and their read-merge-write optimistic
-concurrency posture, but the apply-time guard here is on ``driver_id``
itself (``.is_("driver_id", "null")``) rather than a metadata-column
equality check -- the field actually being raced on is the one that
matters.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from ..supabase_client import supabase
    from .booking_import_service import PAYOUT_LABEL, PAYOUT_TYPE, payout_id_for, recount_drivers
except ImportError:
    from services.booking_import_service import (  # type: ignore
        PAYOUT_LABEL,
        PAYOUT_TYPE,
        payout_id_for,
        recount_drivers,
    )
    from supabase_client import supabase  # type: ignore

REPAIR_META_KEY = "driver_repair"
ZERO = Decimal("0")

# Bounded worker count for the concurrent per-row apply below -- same value
# as migration_data_quality_service._APPLY_POOL_WORKERS and its siblings.
_APPLY_POOL_WORKERS = 20


def _to_decimal(raw: Any) -> Decimal:
    if raw in (None, ""):
        return ZERO
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return ZERO


@dataclass
class DriverRepairCandidate:
    ride_id: str
    old_driver_id: str
    driver_id: str  # newly-matched current drivers.id
    driver_earnings: Decimal
    arrived_at: str | None
    started_at: str | None
    completed_at: str | None
    legacy_import_metadata: dict[str, Any]


@dataclass
class DriverRepairPlan:
    candidates: list[DriverRepairCandidate] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _rows_missing_driver_with_old_id() -> list[dict[str, Any]]:
    """Completed rides with driver_id null AND a recorded old_driver_id --
    only these are even theoretically repairable. A ride whose source
    booking never had a driver at all (old_driver_id blank) has nothing to
    re-match against; it's a genuine missing_driver anomaly, not a
    not-yet-imported one, and stays out of this tool's stats entirely (it's
    still covered by migration_data_quality_service's flag). Read-only."""
    rows = (
        supabase.table("rides")
        .select("id,legacy_import_metadata,driver_earnings,driver_arrived_at,ride_started_at,ride_completed_at")
        .eq("status", "completed")
        .is_("driver_id", "null")
        .execute()
        .data
        or []
    )
    return [r for r in rows if (r.get("legacy_import_metadata") or {}).get("old_driver_id")]


def _driver_id_by_old_driver_id() -> tuple[dict[str, str], set[str]]:
    """Every current drivers row keyed by the old-system driver id it's
    linked to, checking both linkage shapes (see driver_import_service
    ._has_mongo_driver_history_entry's module comment for why both exist):
    top-level ``legacy_import_metadata.old_driver_id`` (a row created
    directly by either driver importer) and
    ``legacy_import_metadata.mongo_driver_history[].old_driver_id`` (a row
    enriched onto an already-existing driver).

    Returns (resolved, ambiguous): ``resolved`` maps an old_driver_id to
    exactly one current driver id. An old_driver_id claimed by more than one
    current driver row goes into ``ambiguous`` instead and is never guessed
    at -- silently picking one would risk linking a completed trip to the
    wrong driver, which is worse than leaving it unmatched. Read-only.
    """
    rows = supabase.table("drivers").select("id,legacy_import_metadata").execute().data or []
    claims: dict[str, set[str]] = {}
    for row in rows:
        driver_id = row.get("id")
        if not driver_id:
            continue
        meta = row.get("legacy_import_metadata") or {}
        old_ids: set[str] = set()
        top_level = meta.get("old_driver_id")
        if top_level:
            old_ids.add(str(top_level))
        for entry in meta.get("mongo_driver_history") or []:
            entry_old_id = entry.get("old_driver_id")
            if entry_old_id:
                old_ids.add(str(entry_old_id))
        for old_id in old_ids:
            claims.setdefault(old_id, set()).add(driver_id)

    resolved = {old_id: next(iter(ids)) for old_id, ids in claims.items() if len(ids) == 1}
    ambiguous = {old_id for old_id, ids in claims.items() if len(ids) > 1}
    return resolved, ambiguous


def build_driver_repair_plan() -> DriverRepairPlan:
    """Read-only. Issues no writes.

    Re-matches every currently-missing_driver, old_driver_id-bearing
    completed ride against the CURRENT drivers table (not the raw CSV) --
    a driver added in a later import batch than the ride itself is exactly
    the case this recovers. Safe to re-run: a ride repaired by a prior apply
    no longer has driver_id null, so it simply drops out of the next scan.
    """
    rows = _rows_missing_driver_with_old_id()
    resolved, ambiguous = _driver_id_by_old_driver_id()

    candidates: list[DriverRepairCandidate] = []
    still_unmatched = 0
    ambiguous_skipped = 0
    for row in rows:
        meta = row.get("legacy_import_metadata") or {}
        old_id = str(meta.get("old_driver_id") or "")
        if old_id in ambiguous:
            ambiguous_skipped += 1
            continue
        driver_id = resolved.get(old_id)
        if not driver_id:
            still_unmatched += 1
            continue
        candidates.append(
            DriverRepairCandidate(
                ride_id=row["id"],
                old_driver_id=old_id,
                driver_id=driver_id,
                driver_earnings=_to_decimal(row.get("driver_earnings")),
                arrived_at=row.get("driver_arrived_at"),
                started_at=row.get("ride_started_at"),
                completed_at=row.get("ride_completed_at"),
                legacy_import_metadata=meta,
            )
        )

    plan = DriverRepairPlan(candidates=candidates)
    plan.stats = {
        "rides_missing_driver_with_old_id": len(rows),
        "repairable": len(candidates),
        "still_unmatched": still_unmatched,
        "ambiguous_old_driver_id_skipped": ambiguous_skipped,
    }
    return plan


def _apply_driver_id(cand: DriverRepairCandidate, *, batch: str, now_iso: str) -> bool:
    """Set one ride's driver_id, guarded by `.is_("driver_id", "null")` so a
    concurrent writer that already linked this ride (another repair run, a
    fresh delta import re-matching it, a manual admin edit) is never
    overwritten -- zero rows updated means skip, not conflict-resolve."""
    meta = dict(cand.legacy_import_metadata)
    meta[REPAIR_META_KEY] = {
        "batch": batch,
        "repaired_at": now_iso,
        "repaired_by": "migration_driver_repair",
        "old_driver_id": cand.old_driver_id,
    }
    res = (
        supabase.table("rides")
        .update({"driver_id": cand.driver_id, "legacy_import_metadata": meta, "updated_at": now_iso})
        .eq("id", cand.ride_id)
        .is_("driver_id", "null")
        .execute()
    )
    return bool(res.data)


def apply_driver_repair(plan: DriverRepairPlan, *, batch: str) -> tuple[list[str], int]:
    """Apply every candidate in plan.candidates: link the ride to its
    re-matched driver, reconstruct its Period 2/3 driver_insurance_periods
    rows, write one offsetting `payouts` row per driver so payable_balance
    isn't inflated, and recount each affected driver's total_rides.

    Returns (conflicted_ride_ids, drivers_recounted_count). A conflict means
    the ride's driver_id was no longer null by the time this ran -- skipped,
    never overwritten (see _apply_driver_id).
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    with ThreadPoolExecutor(max_workers=_APPLY_POOL_WORKERS, thread_name_prefix="driver-repair-apply") as pool:
        results = list(
            zip(
                plan.candidates,
                [
                    fut.result()
                    for fut in [pool.submit(_apply_driver_id, c, batch=batch, now_iso=now_iso) for c in plan.candidates]
                ],
                strict=True,
            )
        )

    applied = [cand for cand, ok in results if ok]
    conflicts = [cand.ride_id for cand, ok in results if not ok]

    if not applied:
        return conflicts, 0

    # --- insurance-period reconstruction: mirrors
    # booking_import_service._plan_insurance_periods exactly (same columns,
    # same is_reconstructed marker).
    period_rows: list[dict[str, Any]] = []
    for cand in applied:
        if cand.arrived_at and cand.started_at:
            period_rows.append(
                {
                    "driver_id": cand.driver_id,
                    "period": 2,
                    "ride_id": cand.ride_id,
                    "started_at": cand.arrived_at,
                    "ended_at": cand.started_at,
                    "is_reconstructed": True,
                }
            )
        if cand.started_at and cand.completed_at:
            period_rows.append(
                {
                    "driver_id": cand.driver_id,
                    "period": 3,
                    "ride_id": cand.ride_id,
                    "started_at": cand.started_at,
                    "ended_at": cand.completed_at,
                    "is_reconstructed": True,
                }
            )
    if period_rows:
        supabase.table("driver_insurance_periods").insert(period_rows).execute()

    # --- offsetting payouts: one per driver, summed across this apply's
    # candidates, using the same idempotency key scheme booking_import_
    # service uses at original import time (payout_id_for(batch, driver_id))
    # so a re-run of the same batch never double-neutralizes.
    payout_totals: dict[str, Decimal] = {}
    for cand in applied:
        payout_totals[cand.driver_id] = payout_totals.get(cand.driver_id, ZERO) + cand.driver_earnings

    wanted_ids = [payout_id_for(batch, d) for d in payout_totals]
    existing_ids: set[str] = set()
    if wanted_ids:
        existing = supabase.table("payouts").select("id").in_("id", wanted_ids).execute().data or []
        existing_ids = {r["id"] for r in existing}

    payouts_to_insert = []
    for driver_id, amount in payout_totals.items():
        if amount <= ZERO:
            continue
        pid = payout_id_for(batch, driver_id)
        if pid in existing_ids:
            continue
        payouts_to_insert.append(
            {
                "id": pid,
                "driver_id": driver_id,
                "amount": str(amount),
                "status": "completed",
                "payout_type": PAYOUT_TYPE,
                "bank_name": PAYOUT_LABEL,
                "created_at": now_iso,
                "processed_at": now_iso,
                "updated_at": now_iso,
            }
        )
    if payouts_to_insert:
        supabase.table("payouts").insert(payouts_to_insert).execute()

    recount_drivers(sorted(payout_totals.keys()))

    return conflicts, len(payout_totals)
