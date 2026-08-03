# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch, corporate, payments |
| PR / commit link | (this branch: `claude/a1c-subtier-c-batch-3`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier C (`utils/offer_expiry_reaper.py`, `utils/corporate_low_balance.py`, `utils/orphaned_hold_reconciler.py`) |

## 1. Issue / gap identified

A fresh full-repo re-scope (`pytest tests/ -q --cov=. --cov-report=json`
from `backend/`, since the prior named-file list was stale after batches
1-2 closed) surfaced the current 60-80% band (41 files, down from the
originally-estimated ~54-55). Picked three background-loop utilities ahead
of raw ranking for the same "real-world consequence" reasoning as prior
Sub-tier B/C picks (`reconciliation.py`, `payment_retry.py`):
- `utils/offer_expiry_reaper.py` (61%, 66 stmts) — dispatch-adjacent: the
  durable backstop for offer-timeout timers lost on a pod restart.
- `utils/corporate_low_balance.py` (62%, 64 stmts) — corporate/money-adjacent:
  low-balance email nudges for corporate wallets with auto-topup off.
- `utils/orphaned_hold_reconciler.py` (69%, 91 stmts) — payments-adjacent:
  releases stranded Stripe card-hold authorizations on cancelled rides.

## 2. Root cause

All three already had dedicated test files, but each left its own
`*_loop()` background-loop wrapper function almost entirely untested (only
`orphaned_hold_reconciler.py`'s loop was even *referenced*, and only to
assert it's registered in `core/lifespan.py`, never to exercise its body):

- **`offer_expiry_reaper.py`**: `_reap_tick`'s fetch-exception branch,
  `_CANDIDATE_LIMIT` scan-cap warning, `get_app_settings` exception
  fallback, and the re-dispatch lookup exception were untested; the entire
  `offer_expiry_reaper_loop` function (lock-not-acquired, lock-acquired,
  tick-exception-survives) and `_pod_id` were untested.
- **`corporate_low_balance.py`**: `run_low_balance_tick`'s company-not-found
  branch, malformed-timestamp `ValueError` catch, and one-wallet-failure-
  doesn't-abort-the-batch swallow were untested; the entire
  `corporate_low_balance_loop` function was untested.
- **`orphaned_hold_reconciler.py`**: `find_orphaned_holds`/`_claim`/
  `reconcile_tick` were already extensively covered (17 tests), but
  `orphaned_hold_reconciler_loop` (lock branches, the `CancelledError`
  must-propagate-not-swallow contract, generic-exception counting) and
  `_pod_id` were untested.

## 3. Fix / remediation

Test-only change, three new files:
- `backend/tests/test_offer_expiry_reaper_coverage.py` (8 tests).
- `backend/tests/test_corporate_low_balance_coverage.py` (5 tests).
- `backend/tests/test_orphaned_hold_reconciler_loop_coverage.py` (6 tests).

No application code changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Three new test files only; zero application
  code touched. Each of these three loops is spawned exactly once, from
  `core/lifespan.py`'s startup — confirmed via
  `grep -rn "offer_expiry_reaper_loop\|corporate_low_balance_loop\|orphaned_hold_reconciler_loop" backend --include=*.py | grep -v tests/`.
  No other module calls into these loop functions or their private helpers.
- **Replay-safety contract unchanged**: the new tests assert, rather than
  change, the documented replay-safety guarantees — `offer_expiry_reaper`'s
  Redis-lock-then-atomic-claim layering, `orphaned_hold_reconciler`'s
  `CancelledError`-must-propagate contract (graceful shutdown), and both
  loops' "one tick's exception must not kill the loop" behavior.
- **Money-adjacent (`orphaned_hold_reconciler.py`, `corporate_low_balance.py`)**:
  no test in this batch touches a real Stripe call or DB write — all mock
  at the same `db_supabase`/`send_email`/`release_open_hold` seams the
  existing test files already use.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_offer_expiry_reaper_coverage.py` | New file — 8 tests | Close coverage gap on `utils/offer_expiry_reaper.py` (61% → 94%) |
| `backend/tests/test_corporate_low_balance_coverage.py` | New file — 5 tests | Close coverage gap on `utils/corporate_low_balance.py` (62% → 91%) |
| `backend/tests/test_orphaned_hold_reconciler_loop_coverage.py` | New file — 6 tests | Close coverage gap on `utils/orphaned_hold_reconciler.py` (69% → 90%) |
| `docs/change-log/2026-08-02-a1c-offer-reaper-corp-low-balance-orphaned-hold-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface |
| `ACTION_ITEMS.md` | Sub-tier C section | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_offer_expiry_reaper_coverage.py tests/test_corporate_low_balance_coverage.py tests/test_orphaned_hold_reconciler_loop_coverage.py -q --no-cov` — 19 passed.
- [x] Coverage measured together with each file's existing test suite:
  `pytest tests/test_offer_expiry_reaper_coverage.py tests/test_offer_expiry_reaper.py tests/test_corporate_low_balance_coverage.py tests/test_corporate_low_balance.py tests/test_orphaned_hold_reconciler_loop_coverage.py tests/test_orphaned_hold_reconciler.py --cov=utils.offer_expiry_reaper --cov=utils.corporate_low_balance --cov=utils.orphaned_hold_reconciler --cov-report=term-missing`:
  - `utils/offer_expiry_reaper.py`: **61% → 94%** (66 stmts, 4 missing — dual-import fallback + one defensive line).
  - `utils/corporate_low_balance.py`: **62% → 91%** (64 stmts, 6 missing — dual-import fallback block).
  - `utils/orphaned_hold_reconciler.py`: **69% → 90%** (91 stmts, 9 missing — dual-import fallback + a couple of the batch-processing exception's inner log-format lines).
  47 passed, 0 failed, 0 collisions with the existing test files run alongside them.
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — `8565 passed, 8 skipped, 1 xfailed, 0 failed` (up from 8546 in the prior batch-2 checkpoint). No regressions.
- [x] Blast-radius greps performed for all three loop entrypoints (see §4).
- [x] Reviewed against CLAUDE.md's "Background task safety" convention: confirmed all three loops remain replay-safe per their own docstrings (Redis leader lock as throttle only, atomic DB claim or idempotent downstream call as the real guard) — the new tests pin this, they don't change it.

## 10. What was NOT verified

- Not run against real Redis/Supabase/Stripe — every external call is
  mocked, matching repo convention for this test tier.
- Two harmless `PytestWarning`s remain (`test_pod_id_shape` in two files is
  marked `@pytest.mark.asyncio` via the file-level `pytestmark` despite
  being a sync function) — cosmetic, does not affect pass/fail, left as-is
  rather than restructuring the file layout for one line.
- No visual/UI verification — these are backend-only background loops with
  no frontend surface in this diff.
