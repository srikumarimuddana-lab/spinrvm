"""Legacy ID crosswalk backfill (migration 328, ``legacy_id_crosswalk``).

Populates the durable "old-app ID -> Spinr UUID" record migration 328
shipped schema-only. This pass builds it entirely from linkage Supabase
already has -- the phone-match every importer already resolved, stamped
into ``legacy_import_metadata`` on ``drivers``/``rides``. Per migration
328's own header comment this "just re-encodes the existing phone-match,
not add real cross-referencing value" on its own -- it cannot recover
anything the original phone-match missed (an unmatched driver/rider stays
unmatched). Accepted anyway (product-owner decision, 2026-09-07) as an
immediately-available first pass, because ``apply_crosswalk_backfill`` is
additive/idempotent: ``build_*_crosswalk_plan`` always excludes any old-id
already recorded, so a later pass reading the raw Mongo export CSVs
directly (once available) can enrich the same table with whatever this
pass couldn't resolve, without redesigning anything -- it would call the
same ``apply_crosswalk_backfill`` with plan candidates built from the
export files instead of Supabase.

Two populations, one shared apply path:

- **Drivers** -- ``entity_type='driver'``, ``spinr_user_id=drivers.id``.
  ``old_numeric_driver_id`` comes from a Saskatoon-sourced top-level
  ``old_driver_id`` (``driver_import_service.IMPORT_SOURCE``);
  ``old_mongo_object_id`` comes from a Mongo-sourced top-level
  ``old_driver_id`` (``MONGO_IMPORT_SOURCE``) or a ``mongo_driver_history``
  entry (enriched drivers -- see ``driver_import_service.py``'s own module
  comment on why both linkage shapes exist). A driver can carry both at
  once (a Saskatoon-created driver later enriched from the Mongo export).
- **Riders** -- ``entity_type='rider'``, ``spinr_user_id=users.id``
  (``rides.rider_id``). ``old_mongo_object_id`` only -- riders have no
  numeric-ID CSV. Resolved by grouping every imported ride's
  ``legacy_import_metadata.old_customer_id`` by ``rider_id``. A rider whose
  own rides disagree on ``old_customer_id`` is ambiguous and skipped with a
  warning, never guessed -- a wrong crosswalk entry would silently point a
  real rider's future support/audit lookup at the wrong old-app customer,
  same "never guess" posture ``migration_driver_repair_service.py``'s
  ``ambiguous_old_driver_id_skipped`` already uses for the equivalent
  driver-side case.

Accepted risk, matching this codebase's existing precedent (see
``driver_import.py``'s own module docstring): this table has no unique
constraint on either old-id column, so two concurrent commits could in
theory double-insert a row for the same old id. ``build_*_crosswalk_plan``
excludes anything already recorded, and the admin UI disables the commit
button while a request is in flight -- the same mitigation already
considered "acceptable for an internal ops tool" elsewhere in this
codebase, not a new risk introduced here.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

try:
    from ..supabase_client import supabase
    from .driver_import_service import IMPORT_SOURCE, MONGO_IMPORT_SOURCE
except ImportError:
    from services.driver_import_service import IMPORT_SOURCE, MONGO_IMPORT_SOURCE  # type: ignore
    from supabase_client import supabase  # type: ignore

logger = logging.getLogger(__name__)

# Same bounded worker count as pre_launch_flag_service._APPLY_POOL_WORKERS /
# migration_data_quality_service._APPLY_POOL_WORKERS and their siblings.
_APPLY_POOL_WORKERS = 20


@dataclass
class CrosswalkCandidate:
    entity_type: str  # driver or rider
    spinr_user_id: str
    old_mongo_object_id: str | None
    old_numeric_driver_id: str | None


@dataclass
class CrosswalkPlan:
    candidates: list[CrosswalkCandidate] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    # spinr_user_ids skipped because their own rides disagree on
    # old_customer_id -- rider-side only, always empty for the driver plan.
    ambiguous: list[str] = field(default_factory=list)


def _existing_old_ids() -> tuple[set[str], set[str]]:
    """Every old_mongo_object_id / old_numeric_driver_id already recorded in
    legacy_id_crosswalk -- so a re-run only ever adds rows for an old id
    that isn't there yet. Read-only."""
    rows = (
        supabase.table("legacy_id_crosswalk").select("old_mongo_object_id,old_numeric_driver_id").execute().data or []
    )
    mongo_ids = {r["old_mongo_object_id"] for r in rows if r.get("old_mongo_object_id")}
    numeric_ids = {r["old_numeric_driver_id"] for r in rows if r.get("old_numeric_driver_id")}
    return mongo_ids, numeric_ids


def build_driver_crosswalk_plan() -> CrosswalkPlan:
    """Read-only. One candidate per driver carrying at least one
    not-yet-recorded legacy old-id."""
    existing_mongo, existing_numeric = _existing_old_ids()

    rows = supabase.table("drivers").select("id,legacy_import_metadata").execute().data or []
    candidates: list[CrosswalkCandidate] = []
    already_recorded = 0

    for row in rows:
        meta = row.get("legacy_import_metadata") or {}
        source = meta.get("source")
        top_old_id = meta.get("old_driver_id")

        numeric_id: str | None = None
        mongo_id: str | None = None
        if source == IMPORT_SOURCE and top_old_id:
            numeric_id = str(top_old_id)
        elif source == MONGO_IMPORT_SOURCE and top_old_id:
            mongo_id = str(top_old_id)

        # An enriched driver additionally (or instead) carries a
        # mongo_driver_history entry, regardless of its own top-level
        # source. Most-recent entry wins if more than one exists.
        history = meta.get("mongo_driver_history") or []
        if history and not mongo_id:
            entry_id = history[-1].get("old_driver_id")
            mongo_id = str(entry_id) if entry_id else None

        had_any_old_id = bool(top_old_id) or bool(history)

        if numeric_id and numeric_id in existing_numeric:
            numeric_id = None
        if mongo_id and mongo_id in existing_mongo:
            mongo_id = None

        if not numeric_id and not mongo_id:
            if had_any_old_id:
                already_recorded += 1
            continue

        candidates.append(
            CrosswalkCandidate(
                entity_type="driver",
                spinr_user_id=row["id"],
                old_mongo_object_id=mongo_id,
                old_numeric_driver_id=numeric_id,
            )
        )

    plan = CrosswalkPlan(candidates=candidates)
    plan.stats = {
        "eligible_drivers_found": len(candidates) + already_recorded,
        "new_rows_to_write": len(candidates),
        "already_recorded": already_recorded,
    }
    return plan


def build_rider_crosswalk_plan() -> CrosswalkPlan:
    """Read-only. Groups every imported ride by rider_id and collects the
    distinct old_customer_id value(s) found for that rider."""
    existing_mongo, _ = _existing_old_ids()

    rows = (
        supabase.table("rides")
        .select("rider_id,legacy_import_metadata")
        .filter("legacy_import_metadata->>old_customer_id", "not.is", "null")
        .execute()
        .data
        or []
    )
    by_rider: dict[str, set[str]] = {}
    for row in rows:
        rider_id = row.get("rider_id")
        old_id = (row.get("legacy_import_metadata") or {}).get("old_customer_id")
        if not rider_id or not old_id:
            continue
        by_rider.setdefault(rider_id, set()).add(str(old_id))

    candidates: list[CrosswalkCandidate] = []
    ambiguous: list[str] = []
    already_recorded = 0

    for rider_id, old_ids in by_rider.items():
        if len(old_ids) > 1:
            ambiguous.append(rider_id)
            continue
        old_id = next(iter(old_ids))
        if old_id in existing_mongo:
            already_recorded += 1
            continue
        candidates.append(
            CrosswalkCandidate(
                entity_type="rider",
                spinr_user_id=rider_id,
                old_mongo_object_id=old_id,
                old_numeric_driver_id=None,
            )
        )

    plan = CrosswalkPlan(candidates=candidates, ambiguous=ambiguous)
    plan.stats = {
        "eligible_riders_found": len(candidates) + already_recorded + len(ambiguous),
        "new_rows_to_write": len(candidates),
        "already_recorded": already_recorded,
        "ambiguous_skipped": len(ambiguous),
    }
    return plan


def apply_crosswalk_backfill(candidates: list[CrosswalkCandidate], *, batch: str) -> tuple[int, list[str]]:
    """Insert one legacy_id_crosswalk row per candidate.

    Plain inserts, not a read-merge-write: this table has no prior row for
    any of these ids by construction (build_*_crosswalk_plan already
    excludes anything already recorded) and nothing else ever writes to
    this table, so there is no concurrent-writer race of the shape the
    read-merge-write backfills in this family guard against. See the
    module docstring's "Accepted risk" section for the one race this
    doesn't close and why.

    Returns (written_count, failed_spinr_user_ids) -- a failed insert is
    logged loudly and skipped, never allowed to abort the rest of the
    batch.
    """
    if not candidates:
        return 0, []

    now_iso = datetime.now(timezone.utc).isoformat()

    def _insert_one(cand: CrosswalkCandidate) -> bool:
        try:
            supabase.table("legacy_id_crosswalk").insert(
                {
                    "entity_type": cand.entity_type,
                    "spinr_user_id": cand.spinr_user_id,
                    "old_mongo_object_id": cand.old_mongo_object_id,
                    "old_numeric_driver_id": cand.old_numeric_driver_id,
                    "batch": batch,
                    "imported_at": now_iso,
                }
            ).execute()
            return True
        except Exception:
            logger.error(
                "[CROSSWALK-BACKFILL] insert failed for %s %s",
                cand.entity_type,
                cand.spinr_user_id,
                exc_info=True,
            )
            return False

    with ThreadPoolExecutor(max_workers=_APPLY_POOL_WORKERS, thread_name_prefix="crosswalk-backfill-apply") as pool:
        results = list(
            zip(
                candidates,
                [fut.result() for fut in [pool.submit(_insert_one, c) for c in candidates]],
                strict=True,
            )
        )

    written = sum(1 for _, ok in results if ok)
    failed = [c.spinr_user_id for c, ok in results if not ok]
    return written, failed
