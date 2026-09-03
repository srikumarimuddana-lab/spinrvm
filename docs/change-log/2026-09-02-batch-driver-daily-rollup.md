# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (root-cause N+1 architecture sweep, subtask 2/3) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/n1-query-batching` |
| Related issue or gap ID | Follow-up to the Weekly Payouts latency root-cause audit — same anti-pattern found in a second background loop |

## 1. Issue / gap identified

`rollup_driver_day()` (the daily driver-stats rollup background loop, run
once per day per service area) looped over every active driver for the day
and, per driver, made 2 sequential Supabase round trips before doing any
work: an existence check against `driver_daily_stats` (to decide insert vs.
update) and a `get_driver_by_id` lookup (to attach `service_area_id`). Same
N+1 shape as the payouts issue — latency scales linearly with driver count,
not with actual query volume.

## 2. Root cause

Same class flagged in `CLAUDE.md`'s Performance SLA anti-patterns list
("N+1 Supabase reads in a loop (batch via `.in_()` instead)"), found during
the follow-up sweep after the Weekly Payouts fix. This is a background
loop (not a synchronous admin request), so it doesn't hang a UI, but it
still burns 2× driver-count round trips per run, every replica-safe
invocation, growing with fleet size.

## 3. Fix / remediation

- Batched the two read-only per-driver lookups upfront, once, for all
  drivers in the day's run: `get_rows_batched_in("driver_daily_stats", "driver_id", ...)`
  for existence, `get_rows_batched_in("drivers", "id", ...)` for
  `service_area_id` — replicating `get_driver_by_id`'s soft-delete
  exclusion (`{"deleted_at": {"$notnull": False}}`) so batched results match
  the per-driver lookup's filtering exactly.
- Left the per-driver RPC (`_phase_stats`) and the per-driver write
  (insert/update) as individual calls, run under bounded concurrency
  (`asyncio.Semaphore(_ROLLUP_CONCURRENCY=20)`) via `asyncio.gather` instead
  of a sequential `for` loop. No safe bulk-upsert helper exists in this
  codebase for the update path — `insert_many_ignore_conflicts` uses
  `ignore_duplicates=True`, which would silently skip idempotent recomputes
  on conflict (wrong semantics here); building new bulk-upsert infra was
  judged out of scope for this fix.
- Concurrency is capped at 20 (not unbounded) because this loop runs on
  every replica already, and unbounded fan-out risks exhausting the shared
  DB connection pool under this repo's Background task safety convention.
- Each batched lookup is wrapped in its own try/except that logs
  `logger.error(...)` and degrades to an empty result set rather than
  aborting the whole day's rollup — matches this module's existing
  fail-safe direction (a driver is then treated as new/no-service-area
  instead of losing rollup coverage for the whole day).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `rollup_driver_day`.** No other function in
  `driver_daily_rollup.py` or elsewhere reads `existing_ids` /
  `service_area_by_id`; nothing else in the codebase calls
  `_process_driver` directly.
- The per-driver RPC call and per-driver insert/update logic are
  byte-for-byte unchanged — only extracted into `_process_driver` and given
  a semaphore guard. The 3 result counters (`created`/`updated`/`failed`)
  moved from local ints to a shared dict mutated inside concurrent
  coroutines; safe because asyncio is single-threaded/cooperative — no
  interleaving can occur inside a plain `dict[key] += 1`, unlike a
  preemptively-scheduled thread.
- `get_rows_batched_in` is the same already-tested helper used by subtask 1
  and elsewhere in this codebase (`routes/admin/drivers.py`,
  `services/dispatch_candidates.py`) — not new infrastructure.
- Degrade path is fail-safe, not fail-silent: a batched-lookup failure is
  logged via `logger.error(..., exc_info=True)` (loud, per this repo's
  "don't silently swallow DB errors" rule) and every affected driver falls
  back to being treated as new-with-no-known-service-area, rather than the
  whole rollup aborting.

## 5. User-experience effect

None rider/driver-facing — `rollup_driver_day` is an internal background
job feeding driver-stats aggregates (used by admin dashboards / earnings
summaries), not a live request path. No behavior change to the stats
themselves, only to the number of round trips used to compute them.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_daily_rollup.py` | Added `_ROLLUP_CONCURRENCY=20`; batched existence + driver/service-area lookups upfront via `get_rows_batched_in`; extracted per-driver body into `_process_driver` guarded by a semaphore; replaced sequential `for` loop with `asyncio.gather` | Eliminate the N+1 read pattern; bound concurrent write/RPC fan-out safely |
| `backend/tests/test_driver_daily_rollup.py` | Added `_batched_in` mock helper; updated existing tests to mock `get_rows_batched_in` instead of per-driver `get_rows`/`get_driver_by_id`; added `test_batched_lookup_failure_degrades_but_does_not_abort` | Regression coverage for batching correctness and the fail-safe degrade path |

## 7. Before / after

```python
# Before: 2 sequential round trips per driver, in a plain for loop
for driver_id in all_driver_ids:
    existing = await db_supabase.get_rows("driver_daily_stats", {...})
    driver = await db_supabase.get_driver_by_id(driver_id)
    ...
```

```python
# After: 2 batched round trips total, then bounded-concurrency per-driver work
existing_rows = await db_supabase.get_rows_batched_in("driver_daily_stats", "driver_id", driver_ids_list, {...})
driver_rows = await db_supabase.get_rows_batched_in("drivers", "id", driver_ids_list, {...})
...
await asyncio.gather(*(_process_driver(d) for d in driver_ids_list))  # each guarded by Semaphore(20)
```

## 8. Rollback plan

`git-revert-safe`. No migration, no schema change, no new table. The
per-driver RPC/write logic is unchanged in substance (only relocated into
`_process_driver`); reverting restores the fully-sequential loop exactly.

## 9. Verification performed

- [x] Automated tests: `test_driver_daily_rollup.py` (8 tests, including 1
      new) — all pass.
- [x] Broader regression sweep: `test_driver_daily_rollup.py`,
      `test_admin_maintenance_coverage.py`, `test_a_p0_3_gps_oom.py`,
      `test_phase_distance_parity.py`, `test_lifespan_watchdog_coverage.py`
      — 42 passed, 5 failed, 2 skipped. The 5 failures are all in
      `TestAuditLogTopActors` (unrelated audit-log actor aggregation code,
      never touched by this change) — **explicitly re-confirmed** by
      `git stash`-ing this change out entirely and re-running that class
      alone against the prior commit: identical 5/5 failures with the same
      error signatures, proving they are pre-existing and unrelated, not a
      regression from this fix.
- [x] `ruff check` and `ruff format --check` both pass clean on both
      modified files.
- [ ] Manual repro / staging check — not performed, no staging environment
      available in this session.
- [x] Blast-radius grep: confirmed no other function reads the new
      `existing_ids`/`service_area_by_id` locals or calls `_process_driver`.
- [x] Reviewed against CLAUDE.md conventions: reused `get_rows_batched_in`;
      no silent error swallowing (batched-lookup failures are `logger.error`
      + `exc_info=True`, then fail-safe degrade, matching this module's
      existing per-driver failure handling); background-loop replay-safety
      preserved (idempotent existence check still gates insert vs. update).
- [ ] Feature-flagged — not flagged. Justification: pure performance/shape
      change to an internal background job with unchanged output semantics;
      same class of change as PR #4874 and subtask 1, which were treated
      the same way.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — rollup output (created/updated/failed
      counts, written rows) is unchanged; only the network access pattern
      and per-driver work concurrency changed
