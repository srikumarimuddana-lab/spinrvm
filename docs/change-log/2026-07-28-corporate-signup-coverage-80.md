# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR link in description) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b — corporate coverage gap, follow-on to PR #2729 (`corporate_accounts.py`) |

## 1. Issue / gap identified

`backend/routes/corporate_signup.py` (self-serve company signup, M1.3) was measured at ~32-33% test coverage, below the ≥80% target CLAUDE.md sets for `routes/corporate_*.py` (same tier as rides/dispatch because it writes `corporate_accounts` rows and bootstraps the owner membership).

## 2. Root cause

The existing test file (`backend/tests/test_corporate_signup.py`) covered the happy path, the no-email-session rejection, the pending-signup cap, the insert-exception path, the bootstrap-failure rollback path, the best-effort ops-email failure path, and payload validation — but not two error branches: (a) `insert_corporate_account` returning a falsy/id-less row without raising (a degraded-write path distinct from an exception), and (b) the compensating `delete_corporate_account` call itself failing after a `bootstrap_owner` failure (double-failure path).

Note: the coverage figure previously recorded in `ACTION_ITEMS.md` (32-33%) does not match what this session measured before writing any new tests (82%, from the pre-existing 7 tests). The stale figure was likely a module-aggregate number from an earlier point in the corporate-lifecycle-audit work, not a fresh per-file measurement. This change log records the coverage actually measured today, both before and after.

## 3. Fix / remediation

Added three unit tests to `backend/tests/test_corporate_signup.py`:
- `test_signup_insert_returns_no_row_is_503` — `insert_corporate_account` returns `None`; asserts 503 and that `bootstrap_owner`/`log_admin_action` are never awaited.
- `test_signup_insert_returns_row_without_id_is_503` — insert returns a dict lacking `id`; same assertions.
- `test_signup_rollback_delete_failure_still_returns_503_from_bootstrap_error` — `bootstrap_owner` raises AND the compensating `delete_corporate_account` also raises; asserts the original 503 (from the bootstrap failure) still surfaces rather than an unhandled second exception, and that `delete_corporate_account` was attempted.

No production code in `corporate_signup.py` was modified — test-only change.

## 4. Risk & impact on existing functionality

- Test-only change to `backend/tests/test_corporate_signup.py`. No production code touched.
- Blast radius: isolated to this one test file. No other test file imports from it; each test uses `patch()` scoped to the `routes.corporate_signup` module namespace and the existing `_patches()` helper/fixtures already used by the other 7 tests in the file, so no shared state leaks across tests.
- No interaction with the 16 background loops, ride state machine, or money/wallet deltas — this route only writes `corporate_accounts` + bootstraps a membership row, no wallet delta.

## 5. User-experience effect

None. Test-only change; no code path a rider, driver, corporate admin, or internal admin exercises is altered.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_signup.py` | Added 3 unit tests covering the insert-returns-no-row branch and the double-failure (bootstrap fails + rollback delete also fails) branch | Close coverage gap on `routes/corporate_signup.py` per `ACTION_ITEMS.md` A1b / CLAUDE.md coverage-minimums table |
| `ACTION_ITEMS.md` | Updated A1b entry: `corporate_signup.py` line moved from the combined 32-33% bucket to its own line at 89%, with a pointer to this change log | Keep the backlog doc accurate |
| `docs/change-log/2026-07-28-corporate-signup-coverage-80.md` | New change log (this file) | Mandatory Change Impact & Risk Log for a change touching a live-tested surface (corporate) per CLAUDE.md |

## 7. Before / after

Not applicable — purely additive test code, no existing behavior changed.

## 8. Rollback plan

`git revert` is sufficient here: this is a test-only addition with no production code change and no data-layer effect. If a new test proves flaky or wrong, reverting the commit (or deleting the three new test functions) fully restores prior state with no migration, flag, or data remediation needed.

## 9. Verification performed

- [x] Automated tests run: `cd backend && python -m pytest tests/test_corporate_signup.py --cov=routes.corporate_signup --cov-report=term-missing -q` — **10 passed**, 0 failed.
- [x] Coverage measured with real `pytest-cov`, not estimated:
  - Before (existing 7 tests only): `routes/corporate_signup.py` 61 stmts, 11 missing, **82%** covered (missing: 30-40, 122-123, 139-140).
  - After (10 tests, this change): `routes/corporate_signup.py` 61 stmts, 7 missing, **89%** covered (missing: 30-40 only — the dual-import `ImportError` fallback block, which only executes under top-level `python -m backend.server` import resolution, not pytest's `backend.*` package import path; not exercisable from a test without breaking the dual-import convention documented in `CLAUDE.md`).
- [ ] Manual repro steps followed in staging — not applicable, test-only change.
- [x] Blast-radius grep performed: `grep -rn "test_corporate_signup" backend/tests/` and `grep -rn "from tests.test_corporate_signup\|import test_corporate_signup" backend/` — no other file imports from or depends on this test module.
- [x] Reviewed against relevant CLAUDE.md convention: patch target is `routes.corporate_signup.<name>` (module-local import names), matching the file's own dual-import pattern and the pre-existing tests' style; async mocks used per Testing Conventions (`@pytest.mark.anyio`-based fixtures/`test_client`, `AsyncMock`).
- [ ] Feature-flagged — not applicable, test-only change.

Real coverage target reached: **89%**, above the ≥80% CLAUDE.md target for `routes/corporate_*.py`.

## 10. What was NOT verified

- Not run against a real Supabase instance — all Supabase-backed calls (`insert_corporate_account`, `delete_corporate_account`, `count_pending_signups_for_user`, `bootstrap_owner`, `log_admin_action`, `send_transactional_email`) are mocked via `unittest.mock.patch`/`AsyncMock`, consistent with the rest of this test file and the repo's unit-test tier; no integration-tier coverage was added.
- Did not run the full `backend/tests/` suite end-to-end (only the targeted `test_corporate_signup.py` file) — full-suite CI run was left to the PR's CI pipeline; ran only the affected file locally to keep iteration fast, per CLAUDE.md's context-discipline guidance.
- The 7 remaining uncovered lines (dual-import fallback, lines 30-40) were left uncovered deliberately — they are non-pytest-reachable per the documented dual-import convention, not a real functional gap; did not attempt to force coverage of them.
- No production/application bug was found in `corporate_signup.py` while writing these tests (the double-failure and no-row paths both behave correctly — surfacing a clean 503, never masking the failure), so nothing is flagged for a follow-up fix.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, test-only diff)
- [x] Blast radius is stated: isolated to one test file, no other consumers
- [x] No silent behavior change — no production code was changed, so no UX field applies (stated as N/A above with justification)
