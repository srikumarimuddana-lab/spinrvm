# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch, payments, corporate |
| PR / commit link | (this branch: `claude/a1c-subtier-c-batch-4`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier C (`utils/driver_claim_reaper.py`, `utils/preauth_capture.py`, `utils/allowance_reset.py`) |

## 1. Issue / gap identified

Continuing from the batch-3 re-scope list (60-80% band), picked three more
background-loop utilities ahead of raw ranking for the same
real-world-consequence reasoning:
- `utils/driver_claim_reaper.py` (65%, 68 stmts) — dispatch-adjacent:
  releases drivers orphaned by a crashed dispatch claim.
- `utils/preauth_capture.py` (72%, 87 stmts) — payments: captures
  booking-time card holds after the tip window, the only backstop before a
  0%-commission driver eats an uncaptured fare.
- `utils/allowance_reset.py` (68%, 76 stmts) — corporate/money: rolls
  corporate allowance periods forward and zeroes non-rollover budgets.

## 2. Root cause

Each already had a dedicated test file covering its main branching logic
well, but left the same two categories of gap that prior batches found in
sibling loops:

- **`driver_claim_reaper.py`**: `_reap_tick`'s `drivers`-fetch-exception and
  release-call-exception branches were untested; the entire
  `driver_claim_reaper_loop` wrapper (lock branches, tick-exception-
  survives) and `_pod_id` were untested.
- **`preauth_capture.py`**: `_capture_tick`'s fetch-exception branch was
  untested; `_capture_one`'s Meta Purchase-conversion hook (fires on a new
  capture, skipped on an idempotent `already_paid` replay) and the
  receipt-send-exception swallow were untested; the entire
  `preauth_capture_loop` wrapper and the `_pod_id`/`_d`/`_round` helpers
  were untested.
- **`allowance_reset.py`**: `run_allowance_reset_tick`'s no-wallet-found
  skip and one-row-exception-doesn't-abort-batch swallow were untested;
  `_add_one_month`'s day-clamp edge case (Jan 31 -> Feb 28) was untested;
  the entire `allowance_reset_loop` wrapper was untested.

## 3. Fix / remediation

Test-only change, three new files:
- `backend/tests/test_driver_claim_reaper_coverage.py` (7 tests).
- `backend/tests/test_preauth_capture_coverage.py` (10 tests).
- `backend/tests/test_allowance_reset_coverage.py` (7 tests).

No application code changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Three new test files only; zero application
  code touched. All three loops are spawned exactly once from
  `core/lifespan.py`'s startup — confirmed via
  `grep -rn "driver_claim_reaper_loop\|preauth_capture_loop\|allowance_reset_loop" backend --include=*.py | grep -v tests/`.
- **Money-adjacent (`preauth_capture.py`, `allowance_reset.py`)**: no test
  in this batch performs a real Stripe capture or a real
  `corporate_wallet_apply_delta` write — all mock at the same
  `settle_card`/`apply_reset`/`reset_allowance_period` seams the existing
  test files already use. The Meta Purchase-conversion test asserts the
  hook fires/doesn't-fire; it does not assert on Meta's actual payload
  (already covered elsewhere).
- **Dispatch-adjacent (`driver_claim_reaper.py`)**: the release call
  (`set_driver_available`) is the same function whose
  `is_available ⇒ is_online` invariant batch 2 pinned in
  `repositories/driver_repo.py`; this batch's new tests don't re-test that
  invariant, only that the reaper's own exception-handling around the call
  is correct.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_driver_claim_reaper_coverage.py` | New file — 7 tests | Close coverage gap on `utils/driver_claim_reaper.py` (65% → 94%) |
| `backend/tests/test_preauth_capture_coverage.py` | New file — 10 tests | Close coverage gap on `utils/preauth_capture.py` (72% → 94%) |
| `backend/tests/test_allowance_reset_coverage.py` | New file — 7 tests | Close coverage gap on `utils/allowance_reset.py` (68% → 89%) |
| `docs/change-log/2026-08-02-a1c-claim-reaper-preauth-allowance-reset-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface |
| `ACTION_ITEMS.md` | Sub-tier C section | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_driver_claim_reaper_coverage.py tests/test_preauth_capture_coverage.py tests/test_allowance_reset_coverage.py -q --no-cov` — 24 passed.
- [x] Coverage measured together with each file's existing test suite:
  `pytest tests/test_driver_claim_reaper_coverage.py tests/test_driver_claim_reaper.py tests/test_preauth_capture_coverage.py tests/test_preauth_capture.py tests/test_allowance_reset_coverage.py tests/test_c_allowance_reset_atomic.py tests/test_corporate_allowance_reset.py --cov=utils.driver_claim_reaper --cov=utils.preauth_capture --cov=utils.allowance_reset --cov-report=term-missing`:
  - `utils/driver_claim_reaper.py`: **65% → 94%** (68 stmts, 4 missing — dual-import fallback + one defensive line).
  - `utils/preauth_capture.py`: **72% → 94%** (87 stmts, 5 missing — dual-import fallback + one defensive line).
  - `utils/allowance_reset.py`: **68% → 89%** (76 stmts, 8 missing — dual-import fallback block).
  43 passed, 0 failed, 0 collisions with the existing test files run alongside them.
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — `8711 passed, 8 skipped, 1 xfailed, 0 failed` (up from 8565 in the prior batch-3 checkpoint; the branch also picked up other merged main commits' tests in between, e.g. #3341, accounting for more of the delta than this batch's 24 new tests alone). No regressions.
- [x] Blast-radius greps performed for all three loop entrypoints (see §4).
- [x] Reviewed against CLAUDE.md's "Background task safety" convention: confirmed all three loops remain replay-safe per their own docstrings — the new tests pin this, they don't change it.

## 10. What was NOT verified

- Not run against real Redis/Supabase/Stripe — every external call is
  mocked, matching repo convention for this test tier.
- Several harmless `PytestWarning`s remain (a few sync helper tests are
  marked `@pytest.mark.asyncio` via file-level `pytestmark`) — cosmetic,
  does not affect pass/fail, left as-is rather than restructuring file
  layout for a handful of lines (same call made in the batch-3 change-log).
- No visual/UI verification — these are backend-only background loops with
  no frontend surface in this diff.
