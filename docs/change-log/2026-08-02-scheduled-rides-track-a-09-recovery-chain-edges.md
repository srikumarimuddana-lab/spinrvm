# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #15 |

## 1. Issue / gap identified

Two narrow, previously-untested seams in `_dispatch_scheduled_ride`'s
recovery design:
1. `match_driver_to_ride` documents itself as "never raises," but nothing
   verified that the timeout safety net (`ride_search_timeout`) still arms
   if that contract were ever violated — a violation would silently fall
   back to relying only on the independent 5-minute stuck-ride sweeper.
2. `_preauthorize_ride_card`'s genuine-exception path (a real Stripe/network
   fault, not the documented decline-degrades-to-`{}` case) was never
   exercised by a test — only the decline path was.

## 2. Root cause

Both were reasoned-about-as-safe based on the called functions' own
documented contracts, but neither was defended against in code (for #1) or
verified by test (for both) — "the docstring says it's safe" is not the
same as "a violation is handled gracefully."

## 3. Fix / remediation

- **Code fix (#1)**: wrapped the `match_driver_to_ride` call in its own
  try/except inside `_dispatch_scheduled_ride`. On an exception, log at
  error level and continue — `asyncio.create_task(ride_search_timeout(...))`
  now runs unconditionally afterward, regardless of whether matching itself
  raised, so a scheduled ride that's genuinely in `searching` status always
  gets the timeout safety net.
- **Test coverage (#1)**: `test_timeout_still_arms_when_match_driver_to_ride_raises`
  — forces `match_driver_to_ride` to raise, asserts `ride_search_timeout` is
  still armed for the ride and dispatch doesn't crash.
- **Test coverage (#2)**: `test_genuine_preauth_exception_does_not_block_dispatch`
  — forces `_preauthorize_ride_card` to raise (not return an empty
  `_PreauthOutcome`), asserts dispatch still proceeds to matching and no
  hold fields are (incorrectly) persisted.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the matching-call section of
  `_dispatch_scheduled_ride`.** No change to the claim, pre-auth, or
  reminder logic. Grepped for other callers of `match_driver_to_ride` from
  this file — only this one call site.
- The behavior change here only manifests if `match_driver_to_ride` ever
  actually violates its own no-raise contract — under normal operation
  (contract held), this is a no-op change (the try/except catches nothing,
  the timeout arms exactly as before). This is defense-in-depth, not a
  functional change to the happy path.
- No interaction with money, corporate billing, or the ride state machine
  beyond ensuring the existing timeout safety net's arming is unconditional.

## 5. User-experience effect

None in the common case. In the narrow case where `match_driver_to_ride`
does raise (currently believed not to happen, per its own contract), a
rider's scheduled ride now reliably gets the same 5-minute no-drivers-found
auto-cancel/refund path as any other ride, instead of depending solely on
the independent stuck-ride sweeper for that same outcome.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/scheduled_rides.py` | `match_driver_to_ride` call wrapped in try/except; `ride_search_timeout` now arms unconditionally | Defense-in-depth if the "never raises" contract is ever violated |
| `backend/tests/test_scheduled_dispatch_cr.py` | New `test_timeout_still_arms_when_match_driver_to_ride_raises` | Pin the fix above |
| `backend/tests/test_scheduled_preauth.py` | New `test_genuine_preauth_exception_does_not_block_dispatch` | Cover the previously-untested genuine-exception branch (code already handled this correctly; only test coverage was missing) |

## 7. Before / after

```python
# Before
await _rides_matching.match_driver_to_ride(ride_id)
asyncio.create_task(_rides_matching.ride_search_timeout(ride_id))
```

```python
# After
try:
    await _rides_matching.match_driver_to_ride(ride_id)
except Exception as match_err:
    logger.error("... match_driver_to_ride raised ... despite its no-raise contract: %s", match_err, exc_info=True)
asyncio.create_task(_rides_matching.ride_search_timeout(ride_id))  # unconditional
```

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully
restores prior behavior.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_scheduled_dispatch_cr.py` +
      `backend/tests/test_scheduled_preauth.py`, both files, 23 passed (21
      prior + 2 new) via the session's venv.
- [x] `ruff check` on all three touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's dispatch/replay-safety conventions.
- [ ] Feature-flagged — not applicable; this is defense-in-depth around an
      already-shipped code path with no new user-visible behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to the happy path — only the (currently
      believed unreachable) exception path changes behavior, from "silent
      reliance on the 5-min sweeper" to "always-armed timeout + a loud log"

## What was NOT verified

The `match_driver_to_ride` "never raises" contract itself was not
re-audited end-to-end in this pass — this fix assumes the contract could in
principle be violated and defends against that, but didn't re-verify every
internal branch of `matching.py` actually upholds it. The import-failure
scenario noted in the original gap review (the dynamic `from routes.rides
import matching` call itself throwing something other than `ImportError`)
remains untested — judged not realistically reachable at runtime (if that
import failed, the app wouldn't have started), so no test was added for it.
