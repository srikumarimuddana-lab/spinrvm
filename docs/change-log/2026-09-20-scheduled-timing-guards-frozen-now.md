# Change Impact & Risk Log — Fix Frozen-`NOW` Staleness in Scheduled-Timing Test Fixture

**Date:** 2026-09-20
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** backend (test-only — no production code changed)
**Domain:** dispatch (scheduled-ride retry/timeout test coverage)

## Issue/gap identified
`backend/tests/test_scheduled_timing_guards.py`'s two tests failed intermittently — but only when run as part of the full ~15,000-test backend suite, never in isolation:
```
FAILED tests/test_scheduled_timing_guards.py::test_scheduled_retry_continues_after_normal_attempt_limit[asyncio] - AssertionError: Expected mock to have been awaited once. Awaited 0 times.
FAILED tests/test_scheduled_timing_guards.py::test_timeout_waits_until_pickup_grace_and_rechecks[asyncio] - AssertionError: assert (1 == 2)
```
This had been observed identically across at least 4 separate PRs today (#5512, #5522, #5523, and this repo's own base-branch CI), each time dismissed as "pre-existing, unrelated cross-test flake" since it reproduced regardless of the PR's own diff. It was never previously root-caused.

## Root cause
`NOW = datetime.now(timezone.utc)` was a **module-level constant**, evaluated once at pytest's import/collection time — not at test-run time. Every ride fixture built by `scheduled()` used fixed offsets from this frozen `NOW` (`scheduled_time = NOW+5min`, deadline = `scheduled_time + 300s grace` = `NOW+10min`).

The two failing tests compare that frozen-clock fixture against `datetime.now(timezone.utc)` computed **live**, inside the real production code under test (`_dispatch_retry`, `ride_search_timeout` in `backend/routes/rides/matching.py`). Pytest collects/imports all ~1500+ test modules up front before running any test body, in filename order. `test_scheduled_timing_guards.py` sits ~744th alphabetically, so several minutes of real wall-clock time elapse — running the ~743 preceding test files — between this module's `NOW` being frozen and its test bodies actually executing. That elapsed time silently ate into the 10-minute and 100-second windows the fixture's offsets were tuned for:
- Test 1's `scheduled_search_deadline` (`NOW+10min`) had already passed by the time `_dispatch_retry` ran its own live `datetime.now()` comparison, so it correctly (per its own logic) stopped retrying — `match_driver_to_ride` was never called, failing the test's own (now-wrong) expectation.
- Test 2 needed `remaining > 500` out of a 600s window; by the time the test executed, `remaining` had shrunk below 500 for the same reason.

Empirically confirmed by a background investigation agent: reproducing both exact failure signatures by simulating elapsed wall-clock time alone (monkeypatching the production code's `datetime.now()` forward, with zero other test files executed), conclusively ruling out cross-test state pollution (no leaking mock, global, or fixture from another file — confirmed no `freezegun`/time-mocking anywhere in `backend/tests/`, and none of the four `autouse=True` conftest fixtures touch anything relevant).

## Fix/remediation
`scheduled()` now computes `now = datetime.now(timezone.utc)` **fresh on every call** (inside the function body) instead of reading a module-level constant frozen at import time. The one remaining call site that referenced the old module-level `NOW` (`test_timeout_claim_precedes_hold_release`) was updated to call `datetime.now(timezone.utc)` directly. This eliminates the staleness at its root: regardless of how much wall-clock time elapses during pytest's collection phase, the fixture and the production code's own `datetime.now()` calls are now always within microseconds of each other, whenever the test actually runs.

No production code (`matching.py`, `scheduled_ride_config.py`) needed to change — it was already behaving correctly against real time; only the test fixture's frozen clock was wrong.

## Risk & impact on existing functionality
- **Blast radius:** isolated to one test file. No other test file references this file's module-level `NOW` (grepped — it was never exported or imported elsewhere).
- This is a pure test-fixture correctness fix — no production dispatch/matching code touched, so no risk to the live ride-dispatch retry/timeout paths themselves.
- This flake has been silently costing CI reliability repo-wide (every PR touching the backend test suite has a real chance of hitting it once the suite's total runtime crosses ~10 minutes before reaching this file) — fixing it benefits every future PR, not just this one.

## User experience effect
None — internal test-only change, never reaches a running app surface.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/tests/test_scheduled_timing_guards.py` | Removed module-level frozen `NOW` constant; `scheduled()` now computes `datetime.now(timezone.utc)` fresh on every call; the one other call site computes it inline too | Fix root cause of a suite-wide-only test flake (frozen-clock staleness), not a cross-test pollution issue |

## Before/after snippet
Before:
```python
NOW = datetime.now(timezone.utc)  # frozen once, at pytest import/collection time

def scheduled(**extra):
    return {..., 'scheduled_time': (NOW + timedelta(minutes=5)).isoformat(), ...}
```

After:
```python
def scheduled(**extra):
    now = datetime.now(timezone.utc)  # fresh every call, at test-run time
    return {..., "scheduled_time": (now + timedelta(minutes=5)).isoformat(), ...}
```

## Rollback plan
`git revert` — test-only change, no runtime behavior affected. Reverting reintroduces the known flake.

## Verification performed
- `pytest tests/test_scheduled_timing_guards.py --no-cov -q` — 4/4 pass.
- `pytest tests/test_scheduled_timing_guards.py tests/test_dispatch_db_errors.py tests/test_dispatch_perf.py tests/test_offer_timeout.py tests/test_migration_concurrently_splitting.py --no-cov -q` — 108/108 pass, no regressions in adjacent dispatch/migration test files.
- Confirmed `scheduled()` returns a different, freshly-computed timestamp on each call (manual timing check with a 1.2s sleep between calls) — proves the staleness bug's root cause is structurally eliminated, not just re-passing by chance.
- Root cause was independently, empirically confirmed by a background investigation agent before this fix was written: reproduced both exact failure signatures by simulating elapsed wall-clock time alone, with zero other test files involved, ruling out cross-test pollution.

## What was NOT verified
- Did not re-run the full ~15,000-test backend suite end-to-end in this session (multi-minute run) to watch these two tests pass at their natural, late (~position 744/1500+) execution point in a real full-suite run — the isolated-call timestamp-freshness check above is a faster, structurally-equivalent substitute, but the definitive end-to-end confirmation is a future full-suite CI run on a PR carrying this fix.
