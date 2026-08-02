# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #13 |

## 1. Issue / gap identified

If `check_scheduled_rides()`'s candidate query fails (e.g. a sustained
Supabase outage), the tick is skipped and logged at error level, but nothing
distinguishes a one-off blip from a sustained outage — every scheduled ride
in the system goes quietly undispatched with only per-tick log lines as the
only trace.

## 2. Root cause

The dispatcher loop (`scheduled_ride_dispatcher_loop`) never inspected the
outcome of `check_scheduled_rides()` — it just called it and moved on.
`check_scheduled_rides()` itself had no return value to inspect.

## 3. Fix / remediation

- `check_scheduled_rides()` now returns `True` (fetch succeeded), `False`
  (fetch failed), or `None` (tick skipped — another replica holds the
  leader lock; this is neither success nor failure and must not affect the
  failure count).
- `scheduled_ride_dispatcher_loop()` tracks `consecutive_fetch_failures` as
  a local variable across loop iterations (mirroring the existing
  `_consecutive_errors` pattern already used in `utils/ws_pubsub.py`'s
  consumer loop). At `_FETCH_FAILURE_ALERT_THRESHOLD` (5) consecutive
  failures, it logs at error level once and increments a new metric
  (`spinr_dispatch_scheduled_fetch_failures_sustained_total`) — once per
  outage, not every tick thereafter. A single success resets the counter.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `check_scheduled_rides()`'s return value and
  `scheduled_ride_dispatcher_loop()`'s loop body.** Grepped for every other
  caller of `check_scheduled_rides()` — only the loop itself and the test
  file (`test_check_queries_scheduled_status`, unaffected: it doesn't
  inspect the return value). No dispatch, claim, matching, or reminder logic
  changed.
- The counter is in-process and resets on replica restart — a deliberate
  choice: this tracks a live, ongoing outage, not a durable record, so
  losing it across a deploy/restart is correct behavior, not a gap.
- No interaction with money, ride state, or corporate billing.

## 5. User-experience effect

None directly. This is pure observability — no rider/driver/admin-facing
behavior changes. (An eventual dashboard surface for this new metric would
be a natural follow-up, same as noted for Finding #03's admin broadcast, but
is out of scope here.)

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/scheduled_rides.py` | `check_scheduled_rides()` now returns `Optional[bool]`; `scheduled_ride_dispatcher_loop()` tracks consecutive failures and alerts once past threshold | Make a sustained candidate-fetch outage visible instead of indistinguishable from a one-off blip |
| `backend/tests/test_scheduled_dispatch_cr.py` | New `TestCheckScheduledRidesReturnValue` (pins the True/False/None contract) and `TestDispatcherLoopFailureAlerting` (pins escalate-once + reset-on-success, driving the loop via a mocked `asyncio.sleep` that raises after N iterations to bound the otherwise-infinite loop) | Regression coverage for the part most likely to silently break: the loop's counting logic |

## 7. Before / after

```python
# Before
async def check_scheduled_rides():
    ...
    except Exception as e:
        logger.error(f"Failed to fetch scheduled rides: {e} ...", exc_info=True)
        return
    ...

async def scheduled_ride_dispatcher_loop():
    while True:
        try:
            await check_scheduled_rides()
        except Exception as e:
            logger.error(...)
        ...
```

```python
# After
async def check_scheduled_rides() -> Optional[bool]:
    ...
    except Exception as e:
        logger.error(...)
        return False
    ...
    return True  # (or None earlier, if the leader lock was held elsewhere)

async def scheduled_ride_dispatcher_loop():
    consecutive_fetch_failures = 0
    while True:
        try:
            result = await check_scheduled_rides()
            if result is False:
                consecutive_fetch_failures += 1
                if consecutive_fetch_failures == _FETCH_FAILURE_ALERT_THRESHOLD:
                    logger.error(...)
                    _metric_inc("spinr_dispatch_scheduled_fetch_failures_sustained_total")
            elif result is True:
                consecutive_fetch_failures = 0
        except Exception as e:
            logger.error(...)
        ...
```

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully
restores prior behavior with no further cleanup.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_scheduled_dispatch_cr.py`, full
      file, 12 passed (7 pre-existing + 5 new) via the session's venv.
- [x] `ruff check` — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's "never silently swallow" convention —
      this is a direct application of it, following the same escalate-once
      shape already used for Finding #03's fix.
- [ ] Feature-flagged — not flagged; purely additive observability with no
      behavior change to what dispatches or when.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — dispatch outcomes are byte-for-byte
      unchanged; only a new signal is added

## What was NOT verified

Not exercised against a real sustained Supabase outage — the failure path
is simulated via a mocked exception, not an actual multi-minute DB blip.
The loop test drives `scheduled_ride_dispatcher_loop()` directly with a
faked `asyncio.sleep` rather than letting it run for real wall-clock time,
which is the standard way to test an otherwise-infinite loop but means the
real ~60s±6s cadence itself wasn't exercised by this test.
