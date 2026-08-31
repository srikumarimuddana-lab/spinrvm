# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Preemptive fix, ahead of the booking/ride-import task — same bug class as Phase 1's driver-import commit-timeout (#4659) and Phase 2's SIN/DOB backfill preempt (#4661), found while auditing `booking_import_service.py` before handing off booking import |

## 1. Issue / gap identified

Auditing `booking_import_service.py` before running the legacy booking/ride
import live found `apply_duration_estimated_backfill()` has the same
sequential-per-row DB round-trip shape already fixed twice this session in
`driver_import_service.py`: a plain `for item in plan.updates:` loop issuing
one `SELECT` + one guarded `UPDATE` per row, both real Supabase HTTP calls,
inside a single synchronous request handler.

## 2. Root cause

Same shape as the two prior fixes: no batching or concurrency across rows,
so total request time scales linearly with row count and can exceed the
request timeout at real data volumes.

## 3. Fix / remediation

Extracted the per-row body into `_apply_one()` and ran it via a bounded
`ThreadPoolExecutor(max_workers=_APPLY_POOL_WORKERS)` (new module constant,
same value — 20 — as `driver_import_service._COMMIT_POOL_WORKERS`). Each row
only ever touches its own `rides.id`, so this changes how long the apply
takes, not what gets written or the optimistic-concurrency guard semantics
(the `duration_estimated IS NULL` guard plus the whole-column
`legacy_import_metadata` snapshot-equality guard, both preserved verbatim).

The rest of `booking_import_service.py` was audited alongside this fix (see
§4) and found already safe — no other change was needed there.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** to `apply_duration_estimated_backfill()`.
  Grepped for callers: only `backend/scripts/backfill_legacy_ride_duration_estimated.py`
  (CLI-only — no admin route/UI exists for this backfill yet), which passes
  the returned conflict list through unchanged.
- **Not on the critical path for the pending booking-import task.**
  `build_plan()` (the function that plans new legacy-ride inserts) already
  writes `legacy_import_metadata.duration_estimated` directly on every new
  row at import time (lines 812/1016) — this backfill only exists to
  retroactively mark rows imported *before* that field existed. Committing
  net-new 08-22 legacy rides does not depend on this fix; it was made
  proactively because the bug was found during the same audit pass.
- Rest of `booking_import_service.py` audited for the same two bug classes
  (sequential per-row loop; intra-batch duplicate resolution) this session,
  partly directly and partly via a read-only background agent covering
  `build_plan()` lines ~525-1178 not read directly. Findings, all
  already-safe / no change needed:
  - `commit_plan()` — batched 200-row inserts, no per-row loop.
  - `recount_drivers()` — prefers a set-based RPC (migration 271); its
    per-driver fallback loop is a documented, intentional compatibility path
    for pre-migration-271 databases, not a new bug.
  - `apply_legacy_vehicle_history_backfill()` — batched inserts (200/call).
  - `build_plan()`'s two per-row CSV loops (lines 545, 849) — no
    `.execute()` calls inside either; all matching happens against
    in-memory dicts from three prefetches done once outside the loops.
  - `build_plan()`'s payout aggregation — grouped into a
    `dict[driver_id, Decimal]` across the whole batch before any row is
    materialized, so two bookings for the same driver in one CSV cannot
    produce colliding payout inserts (mirrors the `pending_*` dedup pattern
    from the driver-import fix, but was already built that way here).
  - `/api/admin/rides/regenerate-imported-snapshots` — already bounded by
    an explicit `limit` param (default 50, max 500), not a one-shot
    unbounded loop.
- All 13 existing tests for this module pass unchanged.

## 5. User-experience effect

- **Internal admin only** (CLI-only today — no admin UI exists for this
  specific backfill). No rider/driver-facing change. Preemptive fix: this
  backfill has not been run against the current CSV export, so there is no
  "before" broken behavior an operator experienced.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/booking_import_service.py` | `apply_duration_estimated_backfill()`: per-row loop replaced with bounded concurrent execution via `_apply_one()` + `ThreadPoolExecutor`; added `_APPLY_POOL_WORKERS` constant and `ThreadPoolExecutor` import | Preempt the same commit-timeout risk found and fixed twice already this session (#4659, #4661) |

## 7. Before / after

```python
# Before
conflicts: list[str] = []
for item in plan.updates:
    existing = supabase.table("rides").select("legacy_import_metadata").eq("id", item.id).execute().data
    ...
    res = supabase.table("rides").update({...}).eq("id", item.id).filter(...).filter(...).execute()
    if not res.data:
        conflicts.append(item.id)
return conflicts
```

```python
# After
def _apply_one(item: DurationEstimatedBackfillItem) -> str | None:
    existing = supabase.table("rides").select("legacy_import_metadata").eq("id", item.id).execute().data
    ...
    res = supabase.table("rides").update({...}).eq("id", item.id).filter(...).filter(...).execute()
    return item.id if not res.data else None

with ThreadPoolExecutor(max_workers=_APPLY_POOL_WORKERS, thread_name_prefix="duration-estimated-backfill-apply") as pool:
    results = [fut.result() for fut in [pool.submit(_apply_one, item) for item in plan.updates]]
return [old_id for old_id in results if old_id is not None]
```

## 8. Rollback plan

`git-revert-safe` — purely a performance/concurrency change, same writes,
same guard semantics.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_legacy_duration_estimated_backfill_service.py` — 13 passed.
- [x] `ruff check` / `ruff format --check` — clean.
- [x] Blast-radius grep: 1 known caller (CLI script only), passes the result through unchanged.
- [x] Audited the rest of `booking_import_service.py` for the same two bug
      classes (sequential per-row loop; intra-batch duplicate resolution) —
      see §4 for the full list of already-safe findings.
- [ ] Not run as a real production build — this is a `backend/` Python
      change; no `npm run build` applies.

## What was NOT verified

- Not yet run against real production — no admin route/UI currently calls
  this function; it is CLI-only, and is not on the critical path for the
  pending booking-import task (see §4).
- No live production timing measurement — there was no "before" failure to
  compare against here (unlike Phase 1's real timeout); this fix is based
  on the demonstrated risk pattern from the two prior fixes, not a repro of
  an actual failure in this function.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (one function, one known caller)
- [x] No silent behavior change to an already-shipped flow — this backfill
      has no admin route/UI yet and has not been run against the current
      export, so there is no working behavior being altered
