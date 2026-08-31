"""Write path for `driver_insurance_period_corrections` (migration 355,
ACTION_ITEMS.md B34) -- the sanctioned way to correct migration 332's
Period-2 reconstruction using `driverlocationlogs.csv`'s real phase-boundary
timestamps, without ever mutating the original (append-only, immutable)
`driver_insurance_periods` row.

Builds directly on top of `insurance_period_reconstruction_verification.py`
(reused unchanged -- `fetch_migration_332_candidate_rides`,
`stream_driverlocationlogs_phase_spans`, `build_verification_plan`) rather
than re-deriving the same classification. That module is deliberately
read-only by its own docstring ("This module is read-only by design"); this
one exists specifically to be the write path it says does not belong there.

Decision this module implements (recorded 2026-08-27,
`docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §5b):
only `DIVERGES` rides get a correction. `CONFIRMED` rides are already
accurate within tolerance and need nothing written. Every other status
(`NO_CSV_DATA`, `AMBIGUOUS_SPAN_COUNT`, `UNKNOWN_PHASE_VALUE`,
`DRIVER_ID_MISMATCH`, `INCOMPLETE_TIMESTAMPS`,
`NO_MIGRATION_332_PROXY_TO_COMPARE`, `EXCLUDED_BY_MIGRATION_332`) has no
single clean real boundary to correct with and is left as-is -- the
existing `is_reconstructed=true` estimate stands, undisturbed, exactly as
disclosed.

Scope: Period 2 only. The verification pass's own real-export run found
Period 3's end boundary already accurate (median 0.6s divergence) --
migration 332's Period 3 reconstruction is not in question and this module
never touches it. Both `corrected_started_at` and `corrected_ended_at` are
written together from the real `going_to_pickup` span for every corrected
row, not just whichever boundary happened to exceed the tolerance --
that's the real, actually-recorded data either way, more accurate than the
proxy on both ends even when only one end technically crossed the 60s
threshold.

Read side already wired, nothing to build there: both consumers this
correction needs to reach already prefer a `driver_insurance_period_
corrections` row over the original span when one exists --
`scripts/compliance_export.py`'s `_fetch_corrections`/`_scan` and
`backend/routes/admin/driver_distance.py`'s `admin_driver_distance_logs`
(both already tested, both already flag `is_corrected` so a regulator or
admin never sees a corrected value indistinguishable from an original
one). This module is the only missing piece: nothing has ever actually
written a row into the table those two already know how to read.

Idempotent / replay-safe: `driver_insurance_period_corrections
_original_period_id_key` (migration 355's UNIQUE index) is the ultimate
guard, but `build_correction_plan` also proactively excludes any
`original_period_id` already present in `already_corrected_period_ids` so
a second run reports `ALREADY_CORRECTED` instead of attempting (and
failing) a duplicate insert.

PIPEDA: never reads or surfaces a raw GPS coordinate -- inherits
`insurance_period_reconstruction_verification`'s `PhaseSpan`/
`RideVerification` types, neither of which ever carries `way_points`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from .insurance_period_reconstruction_verification import VerificationPlan
except ImportError:  # pragma: no cover - CLI convenience
    from insurance_period_reconstruction_verification import VerificationPlan  # type: ignore

# Only a DIVERGES row has both (a) a real, unambiguous single boundary pair
# and (b) an actual disagreement worth correcting. Every other status is
# either not cleanly reconstructable (AMBIGUOUS_SPAN_COUNT, NO_CSV_DATA, ...)
# or already accurate (CONFIRMED) -- see module docstring.
CORRECTABLE_STATUS = "DIVERGES"

DEFAULT_REASON = (
    "GPS-based correction from driverlocationlogs.csv (old app's real "
    "going_to_pickup phase span) -- migration 332's driver_arrived_at proxy "
    "understated Period 2's true start. See docs/change-log/"
    "2026-08-20-insurance-period-reconstruction-verification.md and docs/"
    "migration/2026-08-27-legacy-data-full-migration-approach.md §5b."
)


@dataclass(frozen=True)
class CorrectionRecord:
    ride_id: str
    original_period_id: str
    corrected_started_at: str
    corrected_ended_at: str | None
    reason: str
    corrected_by: str


@dataclass
class CorrectionPlan:
    to_insert: list[CorrectionRecord] = field(default_factory=list)
    # status/reason -> count, for the human-readable report. Includes both
    # verification statuses that were never eligible (CONFIRMED, NO_CSV_DATA,
    # ...) and rides that WERE DIVERGES but got skipped for a plan-building
    # reason (already corrected, missing period-2 row, missing real bounds).
    skipped: dict[str, int] = field(default_factory=dict)

    def _bump(self, key: str) -> None:
        self.skipped[key] = self.skipped.get(key, 0) + 1


def _select_in(
    supabase_client, table: str, columns: str, column: str, values: list[str], chunk: int = 200
) -> list[dict[str, Any]]:
    """Batched ``SELECT ... WHERE column IN (values)``, mirroring
    ``driver_import_service.py``'s ``_select_in`` helper -- same chunk size,
    same reason (avoid an oversized PostgREST URL on a large candidate set,
    even though today's scale, ~186 rides max, never actually needs more
    than one batch)."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(values), chunk):
        batch = values[i : i + chunk]
        if not batch:
            continue
        rows = supabase_client.table(table).select(columns).in_(column, batch).execute().data or []
        out.extend(rows)
    return out


def fetch_period_2_rows_for_rides(supabase_client, ride_ids: list[str]) -> dict[str, dict[str, Any]]:
    """``ride_id -> {"id": <period row id>, "ended_at": ...}`` for every
    ``period=2`` ``driver_insurance_periods`` row among ``ride_ids``.

    A ride can have at most one Period-2 row (migration 332 never wrote
    more than one per ride/period; ``driver_insurance_periods`` has no
    uniqueness constraint enforcing that in the schema, but nothing in this
    codebase's write paths ever produces a second one for the same
    ride+period, and this function takes the last one seen if it somehow
    did rather than guessing which is authoritative).
    """
    if not ride_ids:
        return {}
    rows = _select_in(supabase_client, "driver_insurance_periods", "id,ride_id,period,ended_at", "ride_id", ride_ids)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("period") != 2:
            continue
        rid = row.get("ride_id")
        if rid:
            out[rid] = {"id": row["id"], "ended_at": row.get("ended_at")}
    return out


def fetch_existing_correction_period_ids(supabase_client, period_ids: list[str]) -> set[str]:
    """``original_period_id`` values that already have a correction on
    file -- the proactive half of this module's idempotency guard (the
    UNIQUE index on that column is the ultimate one)."""
    if not period_ids:
        return set()
    rows = _select_in(
        supabase_client, "driver_insurance_period_corrections", "original_period_id", "original_period_id", period_ids
    )
    return {row["original_period_id"] for row in rows if row.get("original_period_id")}


def build_correction_plan(
    verification_plan: VerificationPlan,
    period_2_rows_by_ride: dict[str, dict[str, Any]],
    already_corrected_period_ids: set[str],
    *,
    operator_user_id: str,
    reason: str = DEFAULT_REASON,
) -> CorrectionPlan:
    """Pure -- no I/O, fully unit-testable. Turns the already-computed
    verification classification into a set of correction rows ready to
    insert, per the module docstring's scope decision (DIVERGES only,
    Period 2 only, both boundaries written together).
    """
    plan = CorrectionPlan()
    for r in verification_plan.results:
        if r.status != CORRECTABLE_STATUS:
            plan._bump(r.status)
            continue

        period_row = period_2_rows_by_ride.get(r.ride_id)
        if not period_row:
            # Should not happen for a ride migration 332 actually
            # reconstructed (it always writes Period 2 for a non-excluded
            # ride), but never assume -- a ride whose Period-2 row is
            # missing for any reason has nothing to attach a correction to.
            plan._bump("DIVERGES_BUT_NO_PERIOD_2_ROW")
            continue

        if period_row["id"] in already_corrected_period_ids:
            plan._bump("ALREADY_CORRECTED")
            continue

        real_period2 = r.detail.get("real_period2")
        if not real_period2 or len(real_period2) != 2:
            # build_verification_plan always sets this for a DIVERGES
            # result today, but a correction-writer must not assume a
            # classifier's internal contract holds forever without
            # checking -- fail closed (skip), never fabricate a boundary.
            plan._bump("DIVERGES_BUT_NO_REAL_BOUNDARY")
            continue

        plan.to_insert.append(
            CorrectionRecord(
                ride_id=r.ride_id,
                original_period_id=period_row["id"],
                corrected_started_at=real_period2[0],
                corrected_ended_at=real_period2[1],
                reason=reason,
                corrected_by=operator_user_id,
            )
        )
    return plan


def commit_correction_plan(supabase_client, plan: CorrectionPlan) -> dict[str, int]:
    """Writes ``plan.to_insert`` to ``driver_insurance_period_corrections``.

    One batch INSERT (append-only table, no update/upsert semantics needed
    -- ``build_correction_plan`` already excluded anything with an existing
    correction, and the UNIQUE index on ``original_period_id`` is the final
    backstop if two runs somehow race). Returns ``{"inserted": N}``; raises
    on any DB error rather than swallowing it (CLAUDE.md: never silently
    swallow a DB error) -- a partial-batch failure here needs a human to
    look at exactly what happened, not a script that reports success anyway.
    """
    if not plan.to_insert:
        return {"inserted": 0}
    rows = [
        {
            "original_period_id": rec.original_period_id,
            "corrected_started_at": rec.corrected_started_at,
            "corrected_ended_at": rec.corrected_ended_at,
            "reason": rec.reason,
            "corrected_by": rec.corrected_by,
        }
        for rec in plan.to_insert
    ]
    supabase_client.table("driver_insurance_period_corrections").insert(rows).execute()
    return {"inserted": len(rows)}


def print_correction_report(plan: CorrectionPlan, *, dry_run: bool) -> None:
    mode = "DRY RUN (report only)" if dry_run else "APPLY"
    print(f"{mode} -- legacy insurance-period GPS correction")
    print(f"  TO CORRECT (Period 2, DIVERGES): {len(plan.to_insert)}")
    for status in sorted(plan.skipped):
        print(f"  skipped [{status}]: {plan.skipped[status]}")
    print()
    for rec in plan.to_insert:
        print(
            f"  ride_id={rec.ride_id} original_period_id={rec.original_period_id} "
            f"corrected_started_at={rec.corrected_started_at} corrected_ended_at={rec.corrected_ended_at}"
        )
