# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (this branch: `claude/a1c-subtier-c-batch-6`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier C (`utils/kyb_reverification.py`) |

## 1. Issue / gap identified

`backend/utils/kyb_reverification.py` (corporate KYB re-verification
staleness reminder loop) was at 67% coverage. ACTION_ITEMS.md's top-of-file
summary states A1c/Sub-tier C is "fully CLOSED" (2026-08-10/2026-08-11
entries claim all 39 files in the original 60-80% band are done at ~90%
aggregate) — this file appears to be a gap in that closure sweep (it was
re-verified live via `pytest --cov` before starting, not assumed from the
doc).

## 2. Root cause

`test_kyb_reverification.py` (9 tests) already covers
`run_kyb_reverification_tick`'s happy-path flag/metric emission, the
kill-switch short-circuit, the reflag-cooldown skip/elapsed branches,
custom-threshold pass-through, and one-company-failure-doesn't-block-others.
Uncovered: `resolve_kyb_reverify_threshold_months`'s malformed-value
fallback, `kyb_reverify_cutoff_iso`'s pure date-math, the malformed
`kyb_reverify_flagged_at` timestamp `except ValueError` branch inside the
tick, and the entire `kyb_reverification_loop` wrapper (happy tick,
tick-exception caught/logged/counted).

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_kyb_reverification_coverage.py`
(7 tests) covering the gaps above. No application code changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New test file only; zero application code
  touched. `kyb_reverification_loop` is spawned exactly once, from
  `core/lifespan.py`'s startup — confirmed via
  `grep -rn "kyb_reverification_loop" backend --include=*.py | grep -v tests/`.
  No other module calls into this loop or its private helpers.
- **Corporate-adjacent, non-money**: this loop only flags/logs staleness
  for admin review — it never changes a company's KYB approval status or
  moves money (per the module's own docstring: "NOT automatic
  re-verification, NOT an automatic status change"). The new tests pin
  that read-only contract, they don't change it.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_kyb_reverification_coverage.py` | New file — 7 tests | Close coverage gap on `utils/kyb_reverification.py` (67% → 92%) |
| `docs/change-log/2026-08-12-a1c-kyb-reverification-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface |
| `ACTION_ITEMS.md` | Sub-tier C section | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_kyb_reverification_coverage.py -q --no-cov` — 7 passed.
- [x] Coverage measured together with the existing test files:
  `pytest tests/test_kyb_reverification_coverage.py tests/test_kyb_reverification.py tests/test_corporate_kyb_reverification_route.py --cov=utils.kyb_reverification --cov-report=term-missing` —
  **utils/kyb_reverification.py: 67% → 92%** (75 stmts, 6 missing — the
  dual-import fallback block). 21 passed, 0 failed, 0 collisions.
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — `11074 passed, 8 skipped, 1 xfailed, 0 failed`. No regressions.
- [x] Blast-radius grep performed (see §4).

## 10. What was NOT verified

- Not run against real Supabase — every DB call is mocked, matching repo
  convention for this test tier.
- No visual/UI verification — backend-only background loop, no frontend
  surface in this diff.

## 11. Note on this batch's collision check

This was originally planned as a 28-file batch (per a prior write-only
phase covering the full A1c Sub-tier C re-scope list). Before committing
anything, `git fetch origin main` + a fresh re-read of `ACTION_ITEMS.md`
showed that **27 of the 28 files had already been closed by concurrent
sessions** (in most cases with test files of the exact same name this
session independently chose, e.g. `test_apns_client_coverage.py`,
`test_route_gap_monitor_coverage.py`) — ACTION_ITEMS.md's own summary
states A1c/Sub-tier C reached ~90% aggregate coverage across two prior
closure sweeps (2026-08-03 and 2026-08-10/11). All 27 duplicate draft test
files were discarded without being committed. Only
`utils/kyb_reverification.py` (verified live at 67% via `pytest --cov`,
not assumed from stale documentation) was a genuine remaining gap and is
the sole subject of this PR.
