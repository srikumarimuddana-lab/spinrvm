# Change Impact & Risk Log — Dispatch Retry Attempt-Cap: Stale Test Assertions

**Date:** 2026-09-20
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** backend (test-only — no production code changed)
**Domain:** dispatch (ride matching / offer retry)

## Issue/gap identified
Three tests were failing repo-wide in CI:
- `backend/tests/test_dispatch_perf.py::test_dispatch_retry_stops_at_attempt_cap`
- `backend/tests/test_dispatch_db_errors.py::test_dispatch_retry_respects_attempt_cap`
- `backend/tests/test_offer_timeout.py::test_dispatch_retry_stops_after_max_attempts`

All three assert that once a ride's dispatch retry count passes `_MAX_DISPATCH_ATTEMPTS`, `_dispatch_retry()` must not re-dispatch. They mocked `get_ride`/`db_supabase.get_ride` with an unconfigured `AsyncMock` (no `return_value` set), which returns a default truthy `Mock` object rather than a real ride dict.

## Root cause
`_dispatch_retry()` in `backend/routes/rides/matching.py` was intentionally changed (commit `f03fa22`) to fetch the ride *before* deciding whether the attempt cap applies, so it can check `scheduled_search_deadline()` and exempt scheduled rides from the flat cap. The three tests were never updated for that contract change: with an unconfigured `AsyncMock`, the ride's `status` field reads as a `Mock` attribute (not `"searching"`), so the function's earlier not-ride/wrong-status short-circuit fires first — the tests happened to still pass, but for the wrong reason, and gave zero real coverage of the attempt-cap branch they claim to test. A background investigation agent reproduced this and confirmed which specific check each test was actually exercising.

## Fix/remediation
Each test now mocks `get_ride` to return a real, minimal `SEARCHING`/non-scheduled ride dict (e.g. `{"id": "r1", "status": "searching"}`), so the attempt-cap branch is the one actually reached and exercised. Assertions were tightened to explicitly confirm `get_ride` **is** awaited (the scheduled-ride-exemption check must still run) while `match_driver_to_ride`/the re-dispatch call is **not** awaited past the cap. No production code changed — this is a test-only fix restoring real coverage of an existing, correct behavior.

## Risk & impact on existing functionality
- **Blast radius:** isolated to these three test files. No production code in `backend/routes/rides/matching.py` or any dispatch path was touched.
- These tests are the only callers exercising this specific attempt-cap-vs-scheduled-ride branch; no other test file asserts on `_dispatch_retry`'s post-cap behavior with a real ride dict (grepped for `_dispatch_retry` call sites in `backend/tests/`).
- Independently reviewed by `spinr-dispatch-reviewer` and confirmed safe — the fix correctly exercises the intended branch and does not weaken any assertion.

## User experience effect
None — test-only change, no code path a rider/driver/admin exercises is modified.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/tests/test_dispatch_perf.py` | `test_dispatch_retry_stops_at_attempt_cap` now mocks a real searching, non-scheduled ride; asserts `get_ride` awaited once, `match_driver_to_ride` not awaited | Exercise the actual attempt-cap branch instead of the earlier not-ride short-circuit |
| `backend/tests/test_dispatch_db_errors.py` | `test_dispatch_retry_respects_attempt_cap` same pattern | Same |
| `backend/tests/test_offer_timeout.py` | `test_dispatch_retry_stops_after_max_attempts` same pattern | Same |

## Before/after snippet
Before (`test_dispatch_perf.py`):
```python
with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock()) as get_ride:
    asyncio.run(rides._dispatch_retry("r1", delay=0, attempt=rides._MAX_DISPATCH_ATTEMPTS + 1))
# only implicitly checked no re-dispatch happened; get_ride's unconfigured
# Mock return caused the earlier not-ride/wrong-status short-circuit to fire,
# never actually reaching the attempt-cap check under test
```

After:
```python
searching = {"id": "r1", "status": "searching"}
with (
    patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=searching)) as get_ride,
    patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()) as match,
):
    asyncio.run(rides._dispatch_retry("r1", delay=0, attempt=rides._MAX_DISPATCH_ATTEMPTS + 1))

get_ride.assert_awaited_once_with("r1")
match.assert_not_awaited()
```

## Rollback plan
`git revert` — test-only change, no data/schema/runtime behavior affected.

## Verification performed
- `pytest tests/test_dispatch_db_errors.py tests/test_dispatch_perf.py tests/test_offer_timeout.py --no-cov -q` — 36/36 pass.
- Broader dispatch-area sweep (all dispatch-adjacent test files together) — 710 passed / 4 skipped, no new failures introduced.
- `spinr-dispatch-reviewer` review — confirmed the fix correctly exercises the intended attempt-cap-vs-scheduled-ride branch and does not mask a real bug.

## What was NOT verified
- Not run against a real Supabase instance — this is a unit-test-only fix using the repo's standard `mock_supabase_client`/direct-mock pattern, consistent with how these tests already operated.
- Did not investigate whether other tests elsewhere in the suite have the same unconfigured-`AsyncMock`-hides-the-real-branch pattern; this fix is scoped to the three tests named in the CI failure list.
