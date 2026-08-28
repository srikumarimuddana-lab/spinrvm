# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Preemptive fix, ahead of live Phase 2 execution — same bug class as Phase 1's commit-timeout, fixed before the operator hit it |

## 1. Issue / gap identified

Running Phase 1 (legacy driver import) live in production this session
surfaced a real commit-timeout bug caused by hundreds of fully sequential
per-row DB round-trips within one request (fixed in #4659). Before handing
Phase 2 (SIN/DOB backfill) to the operator, this session audited its
sibling commit function, `apply_legacy_sin_dob_import()`, for the same
pattern — and found it, in a worse form: **up to three** sequential calls
per row (a `SELECT`, an optional `encrypt_driver_pii` RPC, an `UPDATE`),
against `banks.csv`'s real row count (162), putting it in the same
round-trip order of magnitude that already caused a timeout in Phase 1.

## 2. Root cause

Same shape as the Phase 1 timeout bug: a plain sequential `for` loop
issuing independent per-row Supabase calls inside a single HTTP request,
with no batching or concurrency.

## 3. Fix / remediation

Extracted the per-row body into `_apply_one()` and run it via the same
bounded `ThreadPoolExecutor(max_workers=20)` pattern already used and
proven in `commit_mongo_driver_import_plan()`'s fix (reusing the same
`_COMMIT_POOL_WORKERS` constant). Each row updates a different `driver_id`
with no cross-row dependency, so this changes only how long the apply
takes, not what gets written or the race-guard semantics
(`.is_("sin", "null")` / `.is_("date_of_birth", "null")`, which detect a
driver self-entering their own SIN between plan and apply) — verified by
stress test below.

`apply_legacy_vehicle_history_backfill()` (the other Phase 2 backfill) was
also audited and found already safe: its writes are batched inserts (200
rows/call), not a per-row loop, so it needed no change.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** to `apply_legacy_sin_dob_import()`. Grepped
  for its callers — the admin route
  (`routes/admin/legacy_sin_dob_backfill.py`) and the CLI script
  (`scripts/backfill_legacy_driver_sin_dob.py`), both pass the returned
  conflict list through unchanged.
- **Behavior-preserving, not behavior-changing**: verified with a 200-row
  stress test (larger than the real 162-row `banks.csv`) against a fake
  Supabase store with a mix of conflict and non-conflict rows: conflict
  detection exactly matched expectations (rows with a pre-existing SIN
  never overwritten), every non-conflict row got its SIN/DOB/metadata
  marker written correctly, zero mismatches.
- All 42 existing tests for this module and its admin route pass
  unchanged.

## 5. User-experience effect

- **Internal admin only.** No rider/driver-facing change. This is a
  preemptive fix — applied before the operator ran Phase 2 for the first
  time, so there's no "before" broken behavior an admin experienced; it
  simply avoids repeating the Phase 1 timeout pattern.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | `apply_legacy_sin_dob_import()`: per-row loop replaced with bounded concurrent execution via `_apply_one()` + `ThreadPoolExecutor` | Preempt the same commit-timeout risk found and fixed in Phase 1 |

## 7. Before / after

```python
# Before
for upd in plan.updates:
    ...
    res = query.execute()
    if not res.data:
        conflicts.append(upd["old_driver_id"])
return conflicts
```

```python
# After
def _apply_one(upd: dict[str, Any]) -> str | None:
    ...
    res = query.execute()
    return upd["old_driver_id"] if not res.data else None

with ThreadPoolExecutor(max_workers=_COMMIT_POOL_WORKERS, thread_name_prefix="sin-dob-backfill-apply") as pool:
    results = [fut.result() for fut in [pool.submit(_apply_one, upd) for upd in plan.updates]]
return [old_id for old_id in results if old_id is not None]
```

## 8. Rollback plan

`git-revert-safe` — purely a performance/concurrency change, same writes.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_legacy_sin_dob_import_service.py tests/test_admin_legacy_sin_dob_backfill.py` — 42 passed.
- [x] `ruff check` / `ruff format --check` — clean.
- [x] **200-row stress test** against a fake Supabase store with a realistic mix of conflict/non-conflict rows: conflict detection exact, all writes correct, zero mismatches (larger scale than the real 162-row `banks.csv`).
- [x] Audited the sibling vehicle-history apply function too — confirmed already safe (batched inserts), no change needed there.
- [x] Blast-radius grep: 2 known callers, both pass the plan/result through unchanged.

## What was NOT verified

- Not yet run against real production — this is a preemptive fix ahead of
  the operator's first live Phase 2 run this session.
- No live production timing measurement — there was no "before" failure to
  compare against here (unlike Phase 1's timeout, which was diagnosed from
  a real failure); this fix is based on the demonstrated risk pattern from
  Phase 1, not a repro of an actual Phase 2 failure.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (one function, two known callers)
- [x] No silent behavior change to an already-shipped flow — Phase 2 has
      never been run against production yet, so there is no working
      behavior being altered
