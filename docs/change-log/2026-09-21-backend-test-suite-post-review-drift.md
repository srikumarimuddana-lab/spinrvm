# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | backend (tests only — no production code changed) |
| Domain (Sentry tag) | auth, dispatch |
| PR / commit link | (opened alongside this entry) |
| Related issue or gap ID | none pre-existing — found while chasing CI on an unrelated PR (#5616) |

## 1. Issue / gap identified

`ci.yml`'s `backend-test` job has been red on `main` since the 2026-09-20/
2026-09-21 review commits (PR #5602 `9731279`, PR #5614's follow-up `e7cfa20`)
landed: 14 tests fail in the main mocked suite, unrelated to whatever a given
PR actually changes. Confirmed identically on a clean `origin/main` checkout
with zero diff — this blocks `backend-test` for every open and future PR
until fixed, not just the one (#5616) whose CI failure led to this
investigation.

## 2. Root cause

Two independent test-staleness gaps from the same day's review work, plus a
still-open third:

1. **Env-admin revocation (`admin-001`) has no default mock.**
   `utils/env_admin_tokens.py` (migration 434) added a mandatory
   `get_env_admin_token_version()` DB read to `_verify_admin_payload`/
   `admin_refresh`/`admin_logout_all` for the env-credential super admin. It
   deliberately raises `DatabaseError` (503) rather than defaulting to 0 on a
   missing row — correct for production, since a stored 0 would pass every
   token ever minted. But the shared, autouse `mock_supabase_client` fixture
   in `tests/conftest.py` returns an empty list for every table by default,
   so any test that reaches this code path for `admin-001` without itself
   mocking the call now gets an unexpected 503 — 7 tests across 5 files, none
   related to the env-admin feature itself (`test_offer_timeout.py`,
   `test_rides_matching_coverage.py`, `test_p3_admin_jwt_modules.py`,
   `test_admin_token_aud_lockdown.py`, `test_admin_auth_coverage_gap.py`).
2. **`release_driver_and_close_period` consolidation left 5 caller tests
   stale.** The same review consolidated 5 hand-mirrored call sites (see the
   helper's own docstring in `utils/insurance_periods.py`) into one function.
   Most caller tests were updated in the same commit
   (`test_decline_goes_through_the_shared_release_helper` already asserts the
   new call site correctly) but 5 were missed: 3 in
   `tests/test_offer_timeout.py`/`test_rides_matching_coverage.py` still
   mocked the old `set_driver_available`/`record_period_transition` calls
   directly (which the refactored code path no longer makes), and 2 in
   `tests/test_driver_ride_flow_coverage.py` had gone from "correct" to
   "vacuously passing" (asserting `record_period_transition` was "not
   awaited", trivially true once nothing on that path calls it directly
   anymore, regardless of driver state).
3. **A third, separate, order-dependent gap (`tests/
   test_settings_loader_last_known.py`, 2 tests) is NOT fixed by this PR** —
   see "What was NOT verified" below.

`test_logout_all.py::test_refuses_admin_001_super_admin` is a fourth,
distinct case: not broken by (1) or (2), but asserting the exact OLD behavior
(a 400 refusal telling the operator to rotate `ADMIN_PASSWORD`) that the
env-admin-token-version feature explicitly replaced by design — the code's
own comment at the call site says so. Rewritten to assert the new intended
behavior instead of reverting the feature.

## 3. Fix / remediation

- `tests/conftest.py`: new autouse fixture `_default_env_admin_token_version`
  defaults `get_env_admin_token_version()` to 0 (matching the pre-migration-434
  behavior every other test in the suite already assumes) for both
  `dependencies` and `routes.admin.auth`'s separately-imported bindings, and
  `bump_env_admin_token_version()` to return 1. A test that needs the real
  read or a specific version/failure overrides this per-test via
  `monkeypatch.setattr(...)`, same pattern `test_env_admin_token_version.py`
  already uses — unaffected by this change.
- `tests/test_offer_timeout.py`, `tests/test_rides_matching_coverage.py`: 3
  tests updated to mock/assert `release_driver_and_close_period` at the call
  site instead of the pre-refactor `set_driver_available`/
  `record_period_transition` pair.
- `tests/test_driver_ride_flow_coverage.py`: removed 2 now-redundant tests
  (one broken, one vacuously passing) whose scenarios are already covered at
  the correct layer by `tests/test_insurance_release_helper.py`'s
  `test_online_driver_is_closed_out_to_period_1` /
  `test_offline_driver_is_closed_out_to_period_0`.
- `tests/test_logout_all.py`: rewrote `test_refuses_admin_001_super_admin` →
  `test_bumps_env_admin_token_version_for_admin_001_super_admin`, asserting
  the route calls `bump_env_admin_token_version` and returns success, per the
  feature's own documented intent.

## 4. Risk & impact on existing functionality

- **No production code changed.** This PR touches only `tests/conftest.py`
  and 4 test files. Blast radius is the test suite's own accuracy, not any
  runtime behavior.
- **Blast radius of the new autouse fixture**: applies to every test in the
  suite (it's autouse), but only takes effect for code paths that call
  `get_env_admin_token_version`/`bump_env_admin_token_version` through the
  `dependencies` or `routes.admin.auth` module bindings specifically — grepped
  and confirmed those are the only two importers. A test that already
  overrides these names (via its own `monkeypatch.setattr`) is unaffected,
  since `monkeypatch` restores to the true pre-fixture original after the
  test, and a later `setattr` in the same test wins during it.
- Verified no regression via a full local mocked-suite run (15174 passed vs.
  main's 15164 passed + 14 failed — difference reconciles exactly: 12 fixed,
  2 tests removed as redundant, 2 renamed with no count change).

## 5. User-experience effect

None. Test-only change; no production code path is touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/conftest.py` | New autouse fixture defaulting `get_env_admin_token_version`/`bump_env_admin_token_version` | Close the default-mock gap migration 434's feature introduced |
| `backend/tests/test_offer_timeout.py` | 2 tests: mock/assert `release_driver_and_close_period` instead of the pre-refactor calls | Match the 2026-09-20 consolidation |
| `backend/tests/test_rides_matching_coverage.py` | 2 tests: same as above | Match the 2026-09-20 consolidation |
| `backend/tests/test_driver_ride_flow_coverage.py` | Removed 2 redundant/vacuous tests | Scenario already covered by `test_insurance_release_helper.py` |
| `backend/tests/test_logout_all.py` | Rewrote 1 test to assert new intended behavior | Old assertion tested behavior the feature deliberately replaced |

## 7. Before / after

```python
# Before (tests/test_offer_timeout.py) -- asserts a call the refactored code no longer makes
mock_set_available.assert_awaited_once_with("driver_1", available=True)
```
```python
# After -- asserts the consolidated call site instead
mock_release.assert_awaited_once_with("driver_1", reason="offer_timeout", ride_id="ride_1")
```

## 8. Rollback plan

`git revert` is sufficient and complete — this is a test-only change with no
data, schema, or runtime-behavior impact either direction.

## 9. Verification performed

- [x] Automated tests run: the 10 directly affected test files together
  (290 passed) and the full mocked suite (`pytest --cov=. --ignore=tests/
  direct_pool --ignore=tests/rls --ignore=tests/test_schemathesis_fuzz.py`),
  both on this branch and, separately, on a clean `origin/main` checkout for
  comparison (15174 passed / 2 failed here vs. 15164 passed / 14 failed on
  main).
- [x] `ruff check` on all 5 modified files — clean.
- [x] Confirmed via `git diff origin/main` on a separate clean checkout that
  all 14 original failures reproduce identically with zero code difference,
  before writing any fix — ruling out these being caused by the PR (#5616)
  whose CI failure surfaced this.
- [ ] Not run: `tests/direct_pool`, `tests/rls` (unaffected — no file this PR
  touches is imported by either suite).

## What was NOT verified / left open

- **`tests/test_settings_loader_last_known.py::TestFailedReadDoesNotClobber`
  (2 tests) is a separate, still-open, order-dependent failure**, not fixed
  by this PR. It only fails in the full-suite run, not in isolation or in a
  narrower subset — a classic cross-test state-leak signature — but
  `settings_loader.py` has exactly one module global (`_settings_cache`) and
  it's already correctly saved/restored by that test file's own `_isolate_cache`
  autouse fixture. The actual returned dict from `get_app_settings()` is
  missing the mocked row's key entirely (`KeyError`) rather than reflecting
  stale cached data, which doesn't fit a simple cache-not-reset theory and
  wasn't root-caused further given the ~17-minute full-suite iteration cost
  per attempt. Filed as open rather than guessed at.
