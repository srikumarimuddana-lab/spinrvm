# Full-suite result for the error-free Stripe branch (W1–W5)

Follow-up promised by `12568ad`, which corrected three change-log entries that had claimed
"full backend suite before push" when the run was in fact still in flight at push time.

## Result of that run

```
1 failed, 10014 passed, 8 skipped, 1 xfailed, 20 warnings in 647.89s (0:10:47)
FAILED backend/tests/test_loguru_call_conventions.py::test_no_exc_info_kwarg_in_loguru_calls
```

**The branch was pushed with that failure present.** It was caused by this branch's own new
code, not a pre-existing condition — stated plainly because the earlier entries already
overstated verification once.

## The failure

`backend/utils/ledger_projection.py:242` and `:262` called
`logger.error(..., exc_info=True)` against the **loguru** logger.

This is not a style nit. Loguru has no `exc_info` parameter: it is swallowed as a
`str.format` keyword and **no traceback is captured**. Both call sites are the projection
loop's error handlers — the per-item isolation handler and the loop-tick handler — so the
exact paths that exist to explain a failure would have logged a bare message with no stack.

The repo has a guard test for precisely this (`test_no_exc_info_kwarg_in_loguru_calls`);
it did its job. `utils/reconciliation.py` uses `exc_info=True` legitimately because that
module imports stdlib `logging`, not loguru — the guard is loguru-scoped and correctly
ignored it.

## Fix

```python
# Before — kwarg swallowed, no traceback
logger.error("[LEDGER-PROJ] projection raised for event {}", event.get("id"), exc_info=True)

# After — loguru's traceback API
logger.opt(exception=True).error("[LEDGER-PROJ] projection raised for event {}", event.get("id"))
```

Both sites corrected; the rest of the branch's new modules (`ledger_service.py`,
`ledger_repo.py`, `cancellation.py` edits) were grepped and carry no `exc_info` against a
loguru logger.

## Re-verification

- `test_loguru_call_conventions.py` (the test that failed) + `test_ledger_projection.py` +
  `test_replay_safety_payment_loops.py` + `test_log_guard.py` — **65 passed**.
- `ruff check` / `ruff format --check` — clean.
- **Full backend suite re-run against the fixed tree — CLEAN:**

  ```
  10015 passed, 8 skipped, 1 xfailed, 20 warnings in 623.26s (0:10:23)
  SUITE_EXIT=0
  ```

  Zero failures, zero errors. The count is 10,015 vs the failing run's 10,014+1 because the
  previously-failing convention test now passes. This result post-dates every change on the
  branch, including the loguru fix itself.

  (The fix commit was pushed while this run was in flight, and said so rather than claiming
  green — the remote carried the failing code until then, so shipping ahead of the result
  was strictly an improvement. This paragraph is the promised follow-up.)

## Why this wasn't caught earlier

Every targeted battery run during W1–W5 (282, 339, 16, 12, 12) was green, because none of
them include `test_loguru_call_conventions.py` — it is a repo-wide convention scan, not a
payments test. The lesson for this branch: a convention-scan test is only exercised by the
full suite, so "targeted battery green" is not a substitute for it on a branch that adds
new modules.

## Impact assessment

No runtime behavior changed by the fix beyond tracebacks now being captured. The two lines
sit in exception handlers of a loop that is a **no-op in production** (gated off by
`ledger_double_entry_enabled`, and additionally inert until migrations 286/287 are applied),
so no production log line was ever affected.
