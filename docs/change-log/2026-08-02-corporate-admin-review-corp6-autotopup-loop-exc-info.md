# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Corporate #6 |

## 1. Issue / gap identified

`corporate_autotopup_loop`'s top-level error handler
(`utils/corporate_autotopup.py`) caught any exception escaping
`run_autotopup_tick()` and logged only its string form
(`logger.error("autotopup loop error: %s", e)`), discarding the
traceback. Every other error-logging call in this same module (e.g. the
Stripe-error handler inside `run_autotopup_tick` itself) already passes
`exc_info=True`. Per CLAUDE.md's observability convention, a DB error or
an unhandled bug in tick logic (anything outside the already-caught
`stripe.StripeError`) reaching this handler would log with no way to
find where it actually happened.

## 2. Root cause

Inconsistent application of the module's own established logging
convention — the per-wallet Stripe-error handler a few lines above
already does this correctly; the top-level loop handler was just never
brought in line with it.

## 3. Fix / remediation

Added `exc_info=True` to the one logging call:
`logger.error("autotopup loop error: %s", e, exc_info=True)`.

## 4. Risk & impact on existing functionality

- **Blast radius: one log call.** No change to control flow, retry
  behavior, metrics emission (`spinr_bgloop_duration_ms`,
  `spinr_bgloop_errors_total`), or heartbeat recording — all still fire
  identically after the fix.
- Grepped every caller/reader of this log line — it's a leaf statement
  inside the loop's own `except Exception` block; nothing else in the
  codebase parses or depends on this log message's exact format.

## 5. User-experience effect

None rider/driver/admin-facing. Purely an operability improvement: an
on-call engineer investigating an auto-topup outage now sees the full
traceback in the log line instead of just the exception's string
representation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/corporate_autotopup.py` | Added `exc_info=True` to the loop-level error log | Match the module's own established logging convention; surface the traceback for any error not already caught more specifically |
| `backend/tests/test_corporate_autotopup.py` | New test `test_loop_logs_exc_info_on_tick_failure` | Lock in that the fix stays in place |

## 7. Before / after

```python
# Before
except Exception as e:
    logger.error("autotopup loop error: %s", e)
    _had_error = True
```

```python
# After
except Exception as e:
    logger.error("autotopup loop error: %s", e, exc_info=True)
    _had_error = True
```

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully
restores the prior (traceback-discarding) log call. No feature flag —
this is a one-argument logging fix with no behavioral surface to
dark-ship.

## 9. Verification performed

- [x] Automated tests: `test_corporate_autotopup.py` (6, incl. 1 new) —
      run via the session's `/tmp/spinr_venv` venv from repo root.
- [x] `ruff check` on both touched files — clean.
- [x] Blast-radius grep performed (see §4): confirmed this log line has
      no downstream parser/consumer.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Dry-run scenario: `run_autotopup_tick()` raises an unexpected
      `RuntimeError` (e.g. a DB timeout). Before this fix: the log shows
      `"autotopup loop error: <message>"` with no stack trace. After this
      fix: the same message plus the full traceback, confirmed by the
      new test asserting `mock_error.call_args.kwargs["exc_info"] is
      True`.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — single log call, no
      downstream consumers
- [x] No silent behavior change to a working flow — logging verbosity
      only; control flow, metrics, and heartbeat recording unchanged

## What was NOT verified

Not tested against a live Sentry/log-aggregation pipeline to confirm the
traceback actually renders as expected end-to-end — verified only that
`exc_info=True` is passed to the logger call, which is the documented
contract for `logging`/loguru to attach the traceback.
