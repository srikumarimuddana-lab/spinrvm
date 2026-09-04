# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (found while investigating PR #4940's `backend-test` failure) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `fix/error-handling-guards-opt-exception` |
| Related issue or gap ID | none tracked — same class of finding as the `.gitleaksignore` fingerprint fix (#4937): a repo-wide gate failing on every PR for a reason unrelated to any individual PR's diff |

## 1. Issue / gap identified

`backend-test` fails on every PR whose scan touches `main`'s current state:
`tests/test_error_handling_guards.py`'s `TestIncentiveClaimFailsOpen::test_incentive_claim_logs_error`
and 3 tests in `TestRidePathLogLevels` fail with `AssertionError`. Found on
PR #4940, which never touched `backend/` — confirmed pre-existing on `main`
itself before treating it as this PR's problem.

## 2. Root cause

These 4 tests grep the relevant route file's source text for the literal
substring `"logger.error"` near a known log message, to assert the
call logs at ERROR level. Commit `5a6db61` ("fix(logging): close the loguru
gate's re-export blind spot and its 161 offenders") upgraded these 4 call
sites (among 161 others) from `logger.error(...)` to
`logger.opt(exception=True).error(...)` — loguru's pattern for attaching a
full exception traceback, still logging at ERROR level (confirmed against
`test_loguru_call_conventions.py`, this repo's own canonical test for that
convention). `logger.opt(exception=True).error(` does not contain the
literal substring `"logger.error"` (there's `.opt(exception=True).`
between `logger.` and `error`), so the 4 assertions started failing the
moment that commit landed — the source got *better* (a real
traceback-capture improvement), the tests just weren't updated to match.

## 3. Fix / remediation

Widened each of the 4 assertions from `"logger.error" in context` to
`".error(" in context` — a substring both `logger.error(` and
`logger.opt(exception=True).error(` satisfy, so the test still fails if a
call site regresses to a non-error log level (`.warning(`, `.debug(`, or no
log call at all), while accepting either of the two blessed ERROR-logging
forms. `test_cancel_attribution_uses_error`'s separate
`"logger.warning" not in context` assertion is untouched — still catches a
regression to the wrong level.

## 4. Risk & impact on existing functionality

- **Blast radius: one test file, 4 assertions.** No production code
  touched. Grepped for other tests asserting the exact literal
  `"logger.error"` pattern in this same file — none found; the other tests
  in `TestRidePathLogLevels` (`test_cascade_redis_filter_not_debug`, etc.)
  check for the *absence* of a wrong level, not the literal presence
  string, so they're unaffected by this change.
- The widened assertion is strictly more permissive in the one specific
  way needed (accepting `.opt(exception=True).error(` as well as
  `.error(` alone) and no more — it still requires `.error(` to be present,
  so a call site logging at `.warning(`/`.debug(` or not logging at all
  still fails these tests exactly as before.

## 5. User-experience effect

None. Test-only change; no application behavior modified.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_error_handling_guards.py` | Widened 4 assertions from `"logger.error" in context` to `".error(" in context` | Match both blessed ERROR-logging call forms (`logger.error(...)` and `logger.opt(exception=True).error(...)`), not just the older one |

## 7. Before / after

```python
# Before: only matches the older, plain form
assert "logger.error" in context, "incentive claim failure must log at ERROR"

# After: matches both logger.error(...) and logger.opt(exception=True).error(...)
assert ".error(" in context, "incentive claim failure must log at ERROR"
```

## 8. Rollback plan

`git-revert-safe`. No migration, no production code. Reverting restores the
4 failing assertions exactly (i.e. re-breaks `backend-test` for every PR
until re-fixed).

## 9. Verification performed

- [x] Reproduced the original failure first: confirmed via `git blame`
  that `origin/main`'s actual source at all 4 call sites already uses
  `logger.opt(exception=True).error(...)`, and confirmed the commit that
  introduced it (`5a6db61`) was a deliberate, repo-wide logging
  improvement, not a regression to revert.
- [x] `pytest tests/test_error_handling_guards.py` — 30/30 passed
  (previously 4 failing).
- [x] Broader sweep: `pytest tests/test_error_handling_guards.py
  tests/test_loguru_call_conventions.py` — 37/37 passed.
- [x] `ruff check` and `ruff format --check` (0.15.12, the version CI
  pins) both clean on the changed file.
- [x] Blast-radius grep: confirmed no other test in this file depends on
  the literal `"logger.error"` substring in a way this change would
  weaken.
- [ ] Manual repro / staging check — not applicable, test-only change.

## What was NOT verified

- Whether any of the other ~157 call sites `5a6db61` touched have their
  own stale literal-string test assertions elsewhere in the suite — only
  this one file (found via this PR's actual CI failure) was checked. A
  broader sweep for the same pattern across the test suite was judged out
  of scope for this fix (which exists to unblock CI, not to audit every
  consequence of `5a6db61`).
