# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (this branch) |
| Related issue or gap ID | Scheduled-ride audit (`spinr-dispatch-reviewer`), P1 finding #1 |

## 1. Issue / gap identified

The scheduled-ride dispatcher loop's Redis leader lock (`spinr:scheduled_rides:lock`, TTL 90s) outlives the loop's own ~54–66s jittered interval and was never explicitly released — the replica that won the lock fails to re-acquire its own still-live lock on the very next tick and skips it, halving the real dispatch cadence to ~120s on every replica, always, not just under contention.

## 2. Root cause

`check_scheduled_rides()` (`backend/utils/scheduled_rides.py`) acquired the lock via `redis_set_nx(..., ttl=90)` but never called `redis_delete` on it. `scheduled_ride_dispatcher_loop()` sleeps `60 ± 6` seconds between calls. Since the maximum interval (66s) is still less than the 90s TTL, the same replica's own lock is always still live when it wakes for the next tick, so `redis_set_nx` returns `False` (key exists) and that tick is skipped. The lock only actually expires on the tick *after* that (~120s after acquisition), at which point any replica can win it again.

## 3. Fix / remediation

- Lowered the TTL to 45s — comfortably below the loop's minimum jittered interval (54s) — so a missed release still self-heals within well under one cycle, not two.
- Added an explicit `redis_delete` in a `finally` block covering every exit path of `check_scheduled_rides()` (successful tick, fetch-failure early return, and the per-ride loop), so the common case (tick completes normally) frees the lock immediately instead of waiting out the TTL.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `backend/utils/scheduled_rides.py`.** The lock key `spinr:scheduled_rides:lock` is used only inside `check_scheduled_rides()` — confirmed by grep, no other module references this key. The function's only caller is `scheduled_ride_dispatcher_loop()`, registered once in `core/lifespan.py`.
- **No change to the atomic `scheduled → searching` DB claim** (the actual double-dispatch correctness guard) — this lock is a throttle only, per the function's own existing documentation; the fix only changes when/how the throttle itself is held.
- **New failure mode to watch:** if `redis_delete` itself fails (logged as `warning`, non-fatal — the short TTL self-heals), the lock is held for up to 45s instead of being freed immediately. This is strictly better than the pre-fix 90s exposure.

## 5. User-experience effect

- **Riders with a scheduled ride:** dispatch now correctly happens within ~60s of the scheduled time (as originally designed) instead of up to ~120s late. This is a latency improvement, not a new visible flow — no copy/notification change.
- Not visible mid-session; only affects the dispatch-timing window before a scheduled ride's driver search begins.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/scheduled_rides.py` | `check_scheduled_rides()`: lock TTL 90s→45s; added explicit `redis_delete` in a `finally` block around the tick body | Close the self-lockout; restore the intended ~60s dispatch cadence |
| `backend/tests/test_scheduled_rides_coverage.py` | 4 new tests: lock released after success, released even on fetch failure, not released when never acquired, TTL below loop interval | Cover the new release/TTL behavior |

## 7. Before / after

```python
# Before
if not await redis_set_nx("spinr:scheduled_rides:lock", "1", ttl=90):
    return None
# ... tick body ...
return True  # lock never released; self-heals only after 90s
```

```python
# After
_holds_lock = False
try:
    if not await redis_set_nx("spinr:scheduled_rides:lock", "1", ttl=45):
        return None
    _holds_lock = True
except Exception as _lock_err:
    logger.warning(...)

try:
    # ... tick body ...
    return True
finally:
    if _holds_lock:
        try:
            await redis_delete("spinr:scheduled_rides:lock")
        except Exception as _release_err:
            logger.warning(...)  # self-heals via the 45s TTL either way
```

## 8. Rollback plan

- Pure application-logic change, no migration, no data touched. `git revert` is safe and sufficient — the lock key and its semantics are unchanged, only the TTL value and the addition of an explicit release.
- No feature flag needed: this restores previously-intended behavior (the loop's own comments already described a ~60s cadence) rather than introducing new product behavior.

## 9. Verification performed

- [x] Automated tests: `pytest backend/tests/test_scheduled_dispatch_cr.py backend/tests/test_p2_scheduled_rides.py backend/tests/test_scheduled_rides_coverage.py backend/tests/test_scheduled_preauth.py backend/tests/test_scheduled_cancel_notice_fee.py -q --no-cov` — **107 passed, 0 failed**.
- [ ] Manual repro in staging — not performed, no staging/Redis access in this environment.
- [x] Blast-radius grep performed (§4).
- [x] Reviewed against CLAUDE.md's Background Loop Recipe (replay-safe, atomic claim unaffected).

## What was NOT verified

- Not tested against a real Redis instance under actual multi-replica contention — verified via unit tests with mocked `redis_set_nx`/`redis_delete`, not an integration/load test.
- The 45s TTL choice was reasoned from the loop's documented jitter range, not empirically tuned against real tick-duration data.
