# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch, safety |
| PR / commit link | (this branch: `claude/a1c-subtier-c-batch-5`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier C (`utils/period1_distance_finalizer.py`, `utils/driver_online.py`, `utils/presence_sweeper.py`) |

## 1. Issue / gap identified

Continuing from the same re-scope list, three more dispatch/insurance-audit
adjacent files picked ahead of raw ranking:
- `utils/period1_distance_finalizer.py` (64%, 73 stmts) — drains Period-1
  (deadhead) distance accumulators into the append-only insurance-period
  audit table (regulatory, 7-year retention per CLAUDE.md's Saskatchewan
  Regulatory section).
- `utils/driver_online.py` (70%, 33 stmts) — pure functions composing
  driver online-intent + Redis presence into the effective online/available
  state every dispatch reader depends on.
- `utils/presence_sweeper.py` (73%, 33 stmts) — a documented RETIRED no-op
  (see its own module docstring), kept only so its loop-jitter shape stays
  importable for `test_p3_loop_jitter_metrics.py`.

## 2. Root cause

- **`period1_distance_finalizer.py`**: `_driver_left_period1`'s
  active-ride-check exception branch (conservatively returns `False` — "may
  still be in Period 1"), `_finalize_one`/`_pending_accumulators`'s
  `db_supabase.supabase is None` early-return branches, one driver's
  per-row exception not aborting the batch, and the entire
  `period1_distance_finalizer_loop` wrapper (lock branches,
  lock/tick-exception-survives) were untested.
- **`driver_online.py`**: had NO dedicated test file at all despite being
  the pure-function composition every dispatch reader is meant to route
  through (per its own module docstring) — only incidental string matches
  in unrelated test files, confirmed via
  `grep -rl "driver_online" backend/tests/*.py`.
- **`presence_sweeper.py`**: `test_p3_loop_jitter_metrics.py` already
  covers the loop's successful-tick metric emission and two-sleep jitter
  shape, but not the tick-exception branch (`_had_error` ->
  `spinr_bgloop_errors_total`) or the `except asyncio.CancelledError: raise`
  branch (a cancellation from inside the no-op tick must propagate, not be
  swallowed by the broader `except Exception` below it).

## 3. Fix / remediation

Test-only change, three new files:
- `backend/tests/test_period1_distance_finalizer_coverage.py` (7 tests).
- `backend/tests/test_driver_online_coverage.py` (21 tests).
- `backend/tests/test_presence_sweeper_coverage.py` (3 tests).

No application code changed.

## 4. Risk & impact on existing functionality

- **Blast radius**: `period1_distance_finalizer_loop` and
  `presence_sweeper.presence_sweeper_loop` are each referenced from exactly
  one place (`core/lifespan.py` for the finalizer;
  `presence_sweeper_loop` is explicitly NOT scheduled at startup per its
  own docstring — confirmed no `presence_sweeper_loop` call exists outside
  its own module and `test_p3_loop_jitter_metrics.py`).
  `utils/driver_online.py`'s pure functions are imported by dispatch
  readers and admin driver-status surfaces — per
  `grep -rln "from utils.driver_online\|from .driver_online" backend --include=*.py | grep -v tests/`,
  none of those call sites are modified in this PR; the new tests exercise
  the functions directly with synthetic inputs, not through any caller.
- **Regulatory-adjacent (`period1_distance_finalizer.py`)**: no test in
  this batch performs a real Supabase write — all mock at the same
  `db_supabase.supabase`/`db_supabase.run_sync`/`db_supabase.insert_one`
  seams the existing test file already uses. The conservative
  "can't-confirm -> don't finalize yet" behavior on an active-ride-check
  failure is pinned, not changed — a false negative here just delays an
  audit row by one 5-minute tick; a false positive would risk an
  incomplete/duplicate audit total, which the code already guards against
  via the atomic claim.
- **`driver_online.py`** has no setter and no I/O by design (per its own
  docstring: "Do NOT add a setter"); the new tests are pure input/output
  assertions with zero mocking needed.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_period1_distance_finalizer_coverage.py` | New file — 7 tests | Close coverage gap on `utils/period1_distance_finalizer.py` (64% → 88%) |
| `backend/tests/test_driver_online_coverage.py` | New file — 21 tests | Close coverage gap on `utils/driver_online.py` (70% → 100%) |
| `backend/tests/test_presence_sweeper_coverage.py` | New file — 3 tests | Close coverage gap on `utils/presence_sweeper.py` (73% → 94%) |
| `docs/change-log/2026-08-02-a1c-period1-finalizer-driver-online-presence-sweeper-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface |
| `ACTION_ITEMS.md` | Sub-tier C section | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_period1_distance_finalizer_coverage.py tests/test_driver_online_coverage.py tests/test_presence_sweeper_coverage.py -q --no-cov` — 31 passed.
- [x] Coverage measured together with each file's existing test suite:
  `pytest tests/test_period1_distance_finalizer_coverage.py tests/test_period1_distance_finalizer.py tests/test_driver_online_coverage.py tests/test_presence_sweeper_coverage.py tests/test_p3_loop_jitter_metrics.py --cov=utils.period1_distance_finalizer --cov=utils.driver_online --cov=utils.presence_sweeper --cov-report=term-missing`:
  - `utils/period1_distance_finalizer.py`: **64% → 88%** (73 stmts, 9 missing — the dual-import fallback block and the inner query-builder closures of `_finalize_one`/`_pending_accumulators`, which only execute against a real/fully-shaped Supabase mock; the existing `test_period1_distance_finalizer.py` tests already exercise those at a higher level, not re-duplicated here).
  - `utils/driver_online.py`: **70% → 100%** (33 stmts, 0 missing).
  - `utils/presence_sweeper.py`: **73% → 94%** (33 stmts, 2 missing — dual-import fallback block).
  50 passed, 0 failed, 0 collisions with the existing test files run alongside them.
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — `8742 passed, 8 skipped, 1 xfailed, 0 failed` (up from 8711 in the prior batch-4 checkpoint). No regressions.
- [x] Blast-radius greps performed for all three files (see §4).

## 10. What was NOT verified

- Not run against real Redis/Supabase — every external call is mocked,
  matching repo convention for this test tier.
- `utils/presence_sweeper.py` is dead/retired code by design (its own
  module docstring says so); this batch closes its coverage gap for
  completeness since it remains in the 60-80% band, not because it carries
  live production risk.
- No visual/UI verification — backend-only, no frontend surface in this diff.
