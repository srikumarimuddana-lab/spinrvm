"""Verification pass for Oct 30 checklist item #5(a)
(``docs/runbooks/legacy-migration-playbook.md``): re-check migration 332's
Period-2/Period-3 reconstruction for the 186 legacy-imported rides using
``driverlocationlogs.csv``'s real phase-boundary timestamps, instead of the
``driver_arrived_at`` proxy migration 332 used because nothing better was
available at the time.

Source data
-----------
The raw MongoDB export's ``driverlocationlogs.csv`` (one row per driver
phase-span, sometimes linked to a ride via its ``ride_id`` column, which
holds the *old* booking ``_id`` -- the same id ``rides.legacy_import_metadata
->>'old_booking_id'`` already carries for every legacy-imported ride).
Columns used here: ``driver_id`` (old Mongo driver id), ``ride_id`` (old
booking id), ``phase``, ``start_time``/``end_time`` (epoch ms). ``way_points``
is never read into a returned value -- see ``stream_driverlocationlogs_phase_
spans`` below.

Phase mapping (enumerated 2026-08-20 against the real 148 MB export,
7,948 rows -- do not assume this set without re-enumerating against a newer
export; see ``enumerate_distinct_phases``):

  - ``idle``            -- driver online, no ride attached (``ride_id`` is
                            always empty for this phase in the real data).
                            Maps to Period 1 (or Period 0 if actually
                            offline, which this data cannot distinguish) --
                            not used for ride-level reconstruction here.
  - ``going_to_pickup``  -- ride-linked. The old app has no separate
                            "assigned" vs "accepted" vs "arrived" phase, so
                            this single phase spans the whole "en route to /
                            waiting at pickup" window -- i.e. all of
                            CLAUDE.md's Period 2 (`driver_assigned` /
                            `driver_accepted` / `driver_arrived`). Maps
                            entirely to Period 2.
  - ``on_ride``          -- ride-linked, passenger aboard. Maps entirely to
                            Period 3 (`in_progress`).

No other phase value was observed. A phase value outside this set showing
up in a future export is treated conservatively -- excluded from
reconstruction, never guessed at (see ``build_verification_plan``).

Design decision -- why this module never writes to driver_insurance_periods
-----------------------------------------------------------------------------
``driver_insurance_periods`` is append-only, and its immutability trigger
(migration 64, extended by migration 332 to also lock ``is_reconstructed``)
unconditionally blocks any UPDATE once a row's ``ended_at`` is set:

    IF OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION '... already closed and cannot be modified', OLD.id;

Every row migration 332 wrote for these 186 rides is closed (it only ever
wrote finished historical periods, per its own "no row ever left open"
invariant) -- so none of them can ever be corrected in place, no matter
what this module finds.

Inserting a second, competing set of ``driver_insurance_periods`` rows for a
ride migration 332 already covers was considered and rejected. It would
leave a regulator or auditor reading this table for a given ``ride_id``
looking at two overlapping, disagreeing period spans with nothing in the
schema that says which one is authoritative -- and making that
interpretable would mean teaching every consumer of this table
(``scripts/compliance_export.py``, ``backend/routes/admin/driver_distance.py``,
any future coverage-gap-checking logic) to prefer one set over the other.
That is real, cross-surface scope of its own, not something to fold
silently into a verification pass.

``.claude/context/domain-safety.md`` already names the intended fix for
exactly this shape of problem: "Corrections go into a separate
``driver_insurance_period_corrections`` table with justification." That
table has never been built -- confirmed both by grep across ``backend/``
and ``docs/`` and by a live ``information_schema.tables`` query against
production (2026-08-20): no such table exists. Building it is a real
migration + RLS + immutability-trigger + admin-surface change of its own,
out of scope for this pass -- filed as ``ACTION_ITEMS.md`` B34 instead of
built here.

So this module is **read-only by design**: it classifies each of the 186
rides by how well ``driverlocationlogs.csv``'s real phase data lines up
with (or diverges from) migration 332's already-inserted rows, and produces
a report for a human/compliance decision.
``apply_verification_plan`` exists for CLI-shape symmetry with the other
legacy backfill scripts (``backfill_legacy_vehicle_history.py`` et al.) but
always raises -- there is no sanctioned way for this pass to write anything
to ``driver_insurance_periods``, and the Change Impact Log for this pass
(``docs/change-log/2026-08-20-insurance-period-reconstruction-verification.md``)
records the full reasoning.

PIPEDA: this module never surfaces a raw GPS coordinate (latitude/
longitude) anywhere -- not read, logged, or returned. ``way_points`` is not
part of the CSV columns this module reads at all.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIGRATION_332_SOURCE = "legacy_mongo_booking_import"

# The 4 rides migration 332 explicitly excluded from reconstruction (3 with
# no driver_id, 1 with no arrival/start timestamps -- see that migration's
# own header comment). They remain excluded here for the same reasons; this
# module does not re-litigate that exclusion.
EXCLUDED_BY_MIGRATION_332 = frozenset(
    {
        "bda2a258-7987-4344-882e-ca202df17d43",
        "ab5c5f5b-4c3e-4989-90a8-8163b69b08b5",
        "ab0acdfc-46fd-430e-a6e2-502c1a2c7642",
        "e8c7f1b5-84f4-4a64-9f98-1b8ca70ba251",
    }
)

# Phases enumerated against the real export (2026-08-20). See module
# docstring for the full mapping and reasoning.
PHASE_PERIOD_2 = "going_to_pickup"
PHASE_PERIOD_3 = "on_ride"
PHASE_NO_RIDE = "idle"
KNOWN_PHASE_VALUES = frozenset({PHASE_NO_RIDE, PHASE_PERIOD_2, PHASE_PERIOD_3})

# A boundary is only reported as "diverges" past this many seconds -- wide
# enough to absorb clock/logging jitter, far tighter than the ~580s median
# divergence this module actually found for Period 2 starts against the
# real 2026-08-20 export (see the Change Impact Log for the full numbers).
DEFAULT_TOLERANCE_SECONDS = 60


@dataclass(frozen=True)
class PhaseSpan:
    phase: str
    driver_id: str
    start_time_ms: int | None
    end_time_ms: int | None


@dataclass(frozen=True)
class LegacyRideCandidate:
    ride_id: str
    driver_id: str | None
    old_driver_id: str | None
    old_booking_id: str | None
    driver_arrived_at: str | None
    started_at: str | None
    ride_completed_at: str | None
    excluded_by_migration_332: bool = False


@dataclass
class RideVerification:
    ride_id: str
    status: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationPlan:
    results: list[RideVerification] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out


def _parse_epoch_ms(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _epoch_ms_to_dt(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _parse_pg_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if " " in v and "T" not in v:
        v = v.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def enumerate_distinct_phases(csv_path: Path) -> dict[str, int]:
    """Stream the CSV once and count rows per distinct ``phase`` value.

    Never reads ``way_points`` into a returned value. Used to confirm (or
    re-confirm, against a future export) the phase-mapping assumption in
    the module docstring -- callers should not hardcode ``KNOWN_PHASE_
    VALUES`` as ground truth without having run this first against
    whichever export they're actually using.
    """
    csv.field_size_limit(sys.maxsize)
    counts: dict[str, int] = {}
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phase = (row.get("phase") or "").strip()
            counts[phase] = counts.get(phase, 0) + 1
    return counts


def stream_driverlocationlogs_phase_spans(csv_path: Path, booking_ids: set[str]) -> dict[str, list[PhaseSpan]]:
    """Stream ``driverlocationlogs.csv`` row by row, keeping only rows whose
    ``ride_id`` matches one of ``booking_ids`` (the 186 rides' old booking
    ids). Never materializes the whole file in memory and never reads
    ``way_points`` into any value this function keeps, logs, or returns --
    only ``phase``/``driver_id``/``start_time``/``end_time`` survive past
    each row's own iteration.
    """
    csv.field_size_limit(sys.maxsize)
    out: dict[str, list[PhaseSpan]] = {}
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ride_id = (row.get("ride_id") or "").strip()
            if not ride_id or ride_id not in booking_ids:
                continue
            out.setdefault(ride_id, []).append(
                PhaseSpan(
                    phase=(row.get("phase") or "").strip(),
                    driver_id=(row.get("driver_id") or "").strip(),
                    start_time_ms=_parse_epoch_ms(row.get("start_time")),
                    end_time_ms=_parse_epoch_ms(row.get("end_time")),
                )
            )
    return out


def fetch_migration_332_candidate_rides(supabase_client) -> list[LegacyRideCandidate]:
    """Fetch the 186 legacy rides migration 332 covers (182 reconstructed +
    4 explicitly excluded), the same scope migration 332 itself used.

    Two queries (never a cross-table filter PostgREST can't express, per
    CLAUDE.md's query-filter convention): all legacy-imported rides, and
    all ``ride_id``s that already have an ``is_reconstructed=true`` row.
    """
    rides_resp = (
        supabase_client.table("rides")
        .select("id,driver_id,legacy_import_metadata,driver_arrived_at,started_at,ride_completed_at")
        .filter("legacy_import_metadata->>source", "eq", MIGRATION_332_SOURCE)
        .execute()
    )
    periods_resp = (
        supabase_client.table("driver_insurance_periods").select("ride_id").eq("is_reconstructed", True).execute()
    )
    reconstructed_ride_ids = {row["ride_id"] for row in (periods_resp.data or []) if row.get("ride_id")}

    candidates: list[LegacyRideCandidate] = []
    for row in rides_resp.data or []:
        meta = row.get("legacy_import_metadata") or {}
        if meta.get("source") != MIGRATION_332_SOURCE:
            continue
        rid = row["id"]
        excluded = rid in EXCLUDED_BY_MIGRATION_332
        if not (excluded or rid in reconstructed_ride_ids):
            continue
        candidates.append(
            LegacyRideCandidate(
                ride_id=rid,
                driver_id=row.get("driver_id"),
                old_driver_id=meta.get("old_driver_id"),
                old_booking_id=meta.get("old_booking_id"),
                driver_arrived_at=row.get("driver_arrived_at"),
                started_at=row.get("started_at"),
                ride_completed_at=row.get("ride_completed_at"),
                excluded_by_migration_332=excluded,
            )
        )
    return candidates


def build_verification_plan(
    candidates: list[LegacyRideCandidate],
    spans_by_booking: dict[str, list[PhaseSpan]],
    *,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
) -> VerificationPlan:
    """Pure classification -- no I/O, fully unit-testable.

    Per-ride status:
      - ``EXCLUDED_BY_MIGRATION_332`` -- one of the 4 rides migration 332
        itself declined to reconstruct; out of scope here too.
      - ``NO_CSV_DATA`` -- no driverlocationlogs.csv row at all for this
        ride's old_booking_id.
      - ``UNKNOWN_PHASE_VALUE`` -- a phase value outside KNOWN_PHASE_VALUES
        was present for this booking; treated conservatively, never guessed.
      - ``DRIVER_ID_MISMATCH`` -- rows exist for the booking but none carry
        the ride's own old_driver_id (data-integrity concern, not usable).
      - ``AMBIGUOUS_SPAN_COUNT`` -- not exactly one going_to_pickup and one
        on_ride span for this driver+booking; can't derive a single boundary
        without guessing which span is the real one.
      - ``CONFIRMED`` -- real CSV boundaries agree with migration 332's
        existing rows within ``tolerance_seconds`` on every boundary.
      - ``DIVERGES`` -- reconstructable, but at least one boundary disagrees
        with migration 332's rows by more than ``tolerance_seconds``.
    """
    plan = VerificationPlan()
    for c in candidates:
        if c.excluded_by_migration_332:
            plan.results.append(RideVerification(c.ride_id, "EXCLUDED_BY_MIGRATION_332"))
            continue

        spans = spans_by_booking.get(c.old_booking_id or "", [])
        if not spans:
            plan.results.append(RideVerification(c.ride_id, "NO_CSV_DATA"))
            continue

        unknown = sorted({s.phase for s in spans if s.phase not in KNOWN_PHASE_VALUES})
        if unknown:
            plan.results.append(RideVerification(c.ride_id, "UNKNOWN_PHASE_VALUE", {"unknown_phases": unknown}))
            continue

        matching = [s for s in spans if s.driver_id == c.old_driver_id]
        if not matching:
            plan.results.append(
                RideVerification(
                    c.ride_id,
                    "DRIVER_ID_MISMATCH",
                    {"csv_driver_ids_seen": sorted({s.driver_id for s in spans})},
                )
            )
            continue

        gtp = [s for s in matching if s.phase == PHASE_PERIOD_2]
        onride = [s for s in matching if s.phase == PHASE_PERIOD_3]
        if len(gtp) != 1 or len(onride) != 1:
            plan.results.append(
                RideVerification(
                    c.ride_id,
                    "AMBIGUOUS_SPAN_COUNT",
                    {"n_going_to_pickup": len(gtp), "n_on_ride": len(onride)},
                )
            )
            continue

        real_p2_start = _epoch_ms_to_dt(gtp[0].start_time_ms)
        real_p2_end = _epoch_ms_to_dt(gtp[0].end_time_ms)
        real_p3_start = _epoch_ms_to_dt(onride[0].start_time_ms)
        real_p3_end = _epoch_ms_to_dt(onride[0].end_time_ms)
        if None in (real_p2_start, real_p2_end, real_p3_start, real_p3_end):
            plan.results.append(RideVerification(c.ride_id, "INCOMPLETE_TIMESTAMPS"))
            continue

        proxy_arrived = _parse_pg_timestamp(c.driver_arrived_at)
        proxy_started = _parse_pg_timestamp(c.started_at)
        proxy_completed = _parse_pg_timestamp(c.ride_completed_at)
        if not (proxy_arrived and proxy_started and proxy_completed):
            plan.results.append(RideVerification(c.ride_id, "NO_MIGRATION_332_PROXY_TO_COMPARE"))
            continue

        deltas = {
            "p2_start_vs_driver_arrived_at": (real_p2_start - proxy_arrived).total_seconds(),
            "p2_end_vs_started_at": (real_p2_end - proxy_started).total_seconds(),
            "p3_start_vs_started_at": (real_p3_start - proxy_started).total_seconds(),
            "p3_end_vs_ride_completed_at": (real_p3_end - proxy_completed).total_seconds(),
        }
        diverges = any(abs(v) > tolerance_seconds for v in deltas.values())
        status = "DIVERGES" if diverges else "CONFIRMED"
        plan.results.append(
            RideVerification(
                c.ride_id,
                status,
                {
                    "real_period2": [real_p2_start.isoformat(), real_p2_end.isoformat()],
                    "real_period3": [real_p3_start.isoformat(), real_p3_end.isoformat()],
                    "delta_seconds": {k: round(v, 1) for k, v in deltas.items()},
                },
            )
        )
    return plan


def apply_verification_plan(plan: VerificationPlan) -> None:
    """There is no DB write path for this verification pass.

    ``driver_insurance_periods`` is append-only and every migration-332 row
    for these rides is closed (immutable). Inserting a second, competing
    set of rows for an already-covered ride would create a regulator-facing
    contradiction with no authoritative marker -- and the table intended to
    hold a proper correction (``driver_insurance_period_corrections``, per
    ``.claude/context/domain-safety.md``) does not exist. See the module
    docstring and ``docs/change-log/
    2026-08-20-insurance-period-reconstruction-verification.md`` for the
    full reasoning. Always raises; the CLI's ``--apply`` flag is refused.
    """
    raise RuntimeError(
        "insurance_period_reconstruction_verification has no apply path by "
        "design -- see the module docstring and "
        "docs/change-log/2026-08-20-insurance-period-reconstruction-"
        "verification.md. This pass is read-only: it can only report."
    )


def print_verification_report(plan: VerificationPlan, *, dry_run: bool) -> None:
    """Print counts and per-ride status/deltas only -- never a raw GPS
    coordinate (none of PhaseSpan/RideVerification ever carries one)."""
    mode = "DRY RUN (report only)" if dry_run else "REPORT"
    print(f"{mode} -- legacy insurance-period reconstruction verification")
    counts = plan.counts()
    for status in sorted(counts):
        print(f"  {status}: {counts[status]}")
    print(f"  TOTAL: {len(plan.results)}")
    print()
    for r in plan.results:
        if r.status == "DIVERGES":
            print(f"  DIVERGES ride_id={r.ride_id} delta_seconds={r.detail.get('delta_seconds')}")
