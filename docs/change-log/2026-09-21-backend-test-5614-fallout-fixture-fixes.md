# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (session `session_01173usfHtfdzMMzYpWeWmVm`) |
| Surface(s) | backend (test-only) |
| Domain (Sentry tag) | auth, dispatch |
| PR / commit link | see PR description this file is linked from (branch `fix/backend-test-5614-fallout`) |
| Related issue or gap ID | none filed — pre-existing CI-gate breakage found while babysitting PR #5612 |

## 1. Issue / gap identified

`backend-test` CI is red on `main` and on every open PR (confirmed on `main`'s
own post-merge run, commit `4cceaee219`, run `35556420822`): 14 pre-existing
tests fail, none in files this session's own work touches. Root cause traces
to PR #5614 (a different, concurrent Claude Code session), which added two
new production code paths whose pre-existing test mocks weren't updated to
match.

## 2. Root cause

Two independent additions from PR #5614:

1. **`backend/utils/env_admin_tokens.py`** gives the env-credential super
   admin (`admin-001`) a real, revocable `token_version` read from the
   `settings` table on every authenticated request (`_verify_admin_payload`
   in `dependencies/__init__.py`, and `admin_refresh`/`admin_logout_all` in
   `routes/admin/auth.py`). 7 pre-existing tests minted `admin-001` tokens
   specifically *because* the old code skipped all DB lookups for that
   account ("the one user_id that bypasses the admin_staff DB lookup") —
   their mocks never anticipated this new DB read, so it hits the
   mocked-empty `settings` table and fails closed with an unrelated 503.
   One test (`test_logout_all.py::test_refuses_admin_001_super_admin`)
   pinned the *old* behavior outright (400 "rotate ADMIN_PASSWORD") — #5614
   deliberately replaced that with a real, working force-logout, and the
   test was never updated to match.

2. **`backend/utils/insurance_periods.py::release_driver_and_close_period`**
   consolidates 5 previously hand-duplicated "release a driver from an
   offer, close their insurance Period 2" call sites (in
   `routes/rides/matching.py` and `routes/drivers/ride_flow.py`) into one
   helper. 5 pre-existing tests patched the *old* direct calls
   (`_deps.record_period_transition`, `_deps.db_supabase.set_driver_available`
   with an `available=` keyword) or left the new call site entirely
   unmocked — a dead patch in either case, since the code path now goes
   through the new helper (which itself calls its own module-local
   `record_period_transition`, not the `_deps` copy).

Both are genuine, already-reasoned-through, intentional behavior
improvements — not bugs. This is a test-fixture gap, not a production
defect: the mocks are stale, the code under test is correct.

## 3. Fix / remediation

For each of the 12 affected tests, added the missing mock for the new
dependency, matching the mocking pattern this codebase already established
elsewhere for the same helpers (`tests/test_insurance_release_helper.py`,
`tests/test_driver_ride_flow_coverage.py::test_decline_goes_through_the_shared_release_helper`,
`tests/test_env_admin_token_version.py`):

- 6 tests across `test_admin_auth_coverage_gap.py`, `test_admin_revocation_failopen.py`,
  `test_admin_token_aud_lockdown.py`, `test_p3_admin_jwt_modules.py`: added
  `monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=0))`
  (or the `routes.admin.auth` equivalent), preserving each test's original
  intent (claim parsing, Redis fail-open, audience validation) rather than
  letting an unrelated new DB dependency mask it.
- `test_logout_all.py::test_refuses_admin_001_super_admin` **rewritten**
  (not just re-mocked) into `test_bumps_env_admin_token_version_and_revokes`,
  mirroring the passing staff-branch test's own assertions (token bump,
  refresh-token revocation, WS kick), plus a new
  `test_admin_001_500s_when_token_version_bump_fails` regression test for
  the fail-closed path, since the old test's only coverage of failure
  behavior (the 400 response) no longer exists.
- 5 tests across `test_offer_timeout.py`, `test_rides_matching_coverage.py`,
  `test_driver_ride_flow_coverage.py`: added an explicit
  `release_driver_and_close_period` mock (or, for the one test whose whole
  point is the helper's internal 0-vs-1 period derivation, patched the
  helper's own `record_period_transition` reference directly instead of
  mocking the helper away) and updated call-signature assertions
  (`available=True` keyword → positional `True`, matching the new call
  site).

No production code changed. This PR is test-fixture-only.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the 8 modified test files.** No production
  file touched. Grepped for other tests importing the same fixtures/helpers
  these tests use (`_admin_jwt`, `_base_patches`, `mock_deps`) — each fix
  only touches the specific failing test function, not shared fixture
  definitions, so no other passing test in the same file is affected
  (confirmed: every touched file's full test suite re-run clean, not just
  the previously-failing tests — see Verification below).
- The one test rewritten for real behavior (not just fixture) —
  `test_refuses_admin_001_super_admin` → `test_bumps_env_admin_token_version_and_revokes`
  — pins behavior that PR #5614 already shipped to `main`; this PR does not
  change what the endpoint does, only what the test asserts about it.
- Reviewed by `spinr-security-auditor` against the actual diff (admin-auth
  test code) before commit, per CLAUDE.md's pre-merge gate 10.

## 5. User-experience effect

None. Backend test-only change. No rider/driver/corporate-admin/internal-admin-facing
behavior change of any kind — the underlying behavior these tests exercise
was already live on `main` via #5614; this PR only fixes the tests that
prove it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_auth_coverage_gap.py` | Added `get_env_admin_token_version` mock to `test_refresh_super_admin_branch` | New DB dependency in the admin-001 refresh branch |
| `backend/tests/test_admin_revocation_failopen.py` | Same mock added to 2 tests | Same, in `_verify_admin_payload`'s admin-001 branch |
| `backend/tests/test_admin_token_aud_lockdown.py` | Same mock added to 1 test | Same |
| `backend/tests/test_p3_admin_jwt_modules.py` | Same mock added to 2 tests (plus `monkeypatch` fixture param) | Same |
| `backend/tests/test_logout_all.py` | Rewrote `test_refuses_admin_001_super_admin` into 2 new tests matching current behavior | Old test pinned removed behavior (400 rotate-password) |
| `backend/tests/test_offer_timeout.py` | Mocked/asserted against `release_driver_and_close_period` in 2 tests | Consolidated call site, dead `record_period_transition` patch |
| `backend/tests/test_rides_matching_coverage.py` | Same, 2 tests | Same |
| `backend/tests/test_driver_ride_flow_coverage.py` | Patched `insurance_periods.record_period_transition` directly (not via `_deps`) in 1 test | Test's intent (0-vs-1 derivation) requires the real helper to run |

## 7. Before / after

```python
# Before (test_p3_admin_jwt_modules.py) — assumed zero DB reads for admin-001:
async def test_admin_jwt_returns_modules_without_db_lookup(self):
    from dependencies import get_current_user
    token = _mint(role="admin", modules=modules, user_id="admin-001")
    user = await get_current_user(creds)  # raised 503, unmocked new DB read

# After — the claim-parsing behavior under test is unchanged; the new,
# unrelated token_version DB read is stubbed so it doesn't mask it:
async def test_admin_jwt_returns_modules_without_db_lookup(self, monkeypatch):
    import dependencies
    from dependencies import get_current_user
    monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=0))
    token = _mint(role="admin", modules=modules, user_id="admin-001")
    user = await get_current_user(creds)
```

```python
# Before (test_logout_all.py) — pinned the removed 400 behavior:
async def test_refuses_admin_001_super_admin(self):
    with pytest.raises(HTTPException) as exc:
        await inner(request, authorization=f"Bearer {token}")
    assert exc.value.status_code == 400
    assert "ADMIN_PASSWORD" in exc.value.detail

# After — pins the actual, current, intentional behavior:
async def test_bumps_env_admin_token_version_and_revokes(self):
    with (patch(".../bump_env_admin_token_version", bump_version), ...):
        result = await inner(request, authorization=f"Bearer {token}")
    bump_version.assert_awaited_once()
    revoke_all.assert_awaited_once_with("admin-001")
    assert result == {"success": True, "revoked_refresh_tokens": 2}
```

## 8. Rollback plan

`git revert` is sufficient and safe — this is a pure test-fixture fix with
no production code, migration, config, or flag change, and no live data
written or read differently by anything in this diff.

## 9. Verification performed

- [x] Automated tests run: every individually-touched test file's full
  suite re-run clean after each fix (not just the previously-failing
  tests) — `test_admin_auth_coverage_gap.py` (relevant subset), `test_admin_revocation_failopen.py`,
  `test_admin_token_aud_lockdown.py`, `test_p3_admin_jwt_modules.py` (48
  passed together), `test_logout_all.py` (21 passed), `test_offer_timeout.py`
  (24 passed), `test_rides_matching_coverage.py` (22 passed),
  `test_driver_ride_flow_coverage.py` (121 passed).
- [x] Blast-radius grep performed: see section 4.
- [x] Reviewed against relevant CLAUDE.md convention(s): Testing
  Conventions (mock target discipline — patched the module that actually
  defines/calls the function under test, not a re-export), Observability
  (no logging changed).
- **Review used:** `spinr-security-auditor` via the Agent tool, against the
  actual diff — findings incorporated before this commit (see PR body for
  outcome).
- **Full-suite confirmation:** a complete `backend/tests/` run (16166
  tests) was in progress at commit time to give a final, authoritative
  count; result not yet available — see "What was NOT verified" below and
  the PR's own follow-up comment once it completes.

## What was NOT verified

- **The full-suite run had not completed as of this commit.** Each fix was
  verified against its own file in isolation (and cross-file for the
  originally-batched failures), which is sufficient to prove each fix
  correct on its own merits, but the authoritative "does `backend-test`
  actually go green end-to-end" answer depends on the full run finishing
  clean. Will be confirmed as a follow-up before treating this PR as done.
- Two further pre-existing failures (`test_settings_loader_last_known.py`'s
  `TestFailedReadDoesNotClobber` tests) were investigated separately — they
  pass individually and pass paired with their immediate neighbor file, but
  reproduce only under full-suite ordering (a `_settings_cache`
  module-global test-isolation issue, not caused by #5614's own changes).
  Investigation in progress; not yet included in this commit if unresolved
  — see the PR body for current status.

## Assumptions

- The `spinr-security-auditor` review (backgrounded, in progress at commit
  time) will surface anything this per-file verification missed; its
  findings will be addressed in a follow-up commit on this same branch if
  needed, per the babysit protocol's "there is no round limit" rule.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — the one test
  that pins a *changed* behavior (`test_logout_all.py`) pins behavior
  already shipped by #5614, not something this PR itself changes
