# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code (session), on user's explicit go-ahead |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (branch: TBD — see commit) |
| Related issue or gap ID | Found by a `spinr-safety-sos-reviewer` audit of PR #5529 (push_retry.py Android-channel fix), not yet filed as a ticket |

## 1. Issue / gap identified

`backend/utils/safety_checkin_loop.py`'s ride check-in push (the "are you okay?" push sent 20 minutes into every ride) had two related bugs:
1. It called `send_push_notification()` without `target_app="rider"` or `priority="safety"`.
2. Its failure handling only caught a **raised exception** — but `send_push_notification()` never raises for a time-critical priority; it fails closed by returning `False`. A plain delivery failure was silently treated as success.

## 2. Root cause

1. **Missing `target_app`.** `features.py`'s `_build_fcm_message()` (used by `send_push_notification`'s underlying immediate-send path) picks the Android notification channel by `target_app`: `"ride-updates"` for rider, `"ride-offers"` (driver-app-only) otherwise. Without `target_app="rider"`, this always-rider-targeted push defaulted to the driver-only channel — Android silently drops a notification sent to a channel that doesn't exist on the receiving app. This is the same root-cause class just fixed in PR #5529 (`backend/utils/push_retry.py`), in a different call site that PR didn't touch.
2. **Missing `priority="safety"`.** Without it, this call defaults to `priority="normal"`, which is *not* one of the three guaranteed-delivery tiers (`dispatch`/`safety`/`account`). A normal-priority push is subject to the user's push opt-out and quiet-hours/daily-cap throttling, and — more importantly — on an immediate-send failure it does **not** fall back to `push_retry_queue` at all (only the three time-critical tiers do). A transient FCM error meant this safety check-in was dropped once, permanently, with no retry.
3. **Failure-detection gap.** `send_push_notification()`'s documented contract is: never raises for a time-critical priority, always returns `True`/`False`. The loop's `except Exception:` handler (which releases the Redis claim key so a later tick retries) is therefore dead code for the realistic failure path — a normal `False` return was passing the `if rider_id:` block as if the push had succeeded, permanently leaving the `safety:checkin:sent:{ride_id}` claim set and the rider never re-notified.

## 3. Fix / remediation

- Added `priority="safety"` and `target_app="rider"` to the `send_push_notification()` call.
- Captured the return value (`sent_ok`) instead of only catching exceptions; `if not sent_ok:` now triggers the same claim-release-for-retry path regardless of whether the failure was a raised exception or a plain `False` return.
- Kept the `except Exception:` block as defense-in-depth (still logs with a traceback if something genuinely unexpected raises), but it's no longer the only path that detects a failure.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped `backend/` for `safety_checkin_loop` — every other reference is a comment in `route_deviation_alerter.py` and `routes/rides/safety.py` citing this file as a pattern/TTL reference, not an actual import of or call into its internals. No other code calls `_tick()` or the private helpers touched here.
- **What actually changes in behavior:** (a) the check-in push now reaches rider-app's real Android channel instead of a nonexistent one — riders on Android will now actually receive a check-in push that was previously silently dropped; (b) a genuinely failed send now correctly retries on the next 30s tick instead of being permanently (and silently) abandoned; (c) `priority="safety"` also means this push now bypasses the rider's push opt-out/quiet-hours preference, same as it already does for `sos_confirmation` and dispatch offers — this is a deliberate, not accidental, behavior change: a safety check-in should not be silently suppressed by a quiet-hours setting, consistent with how every other safety-tier push in this codebase already behaves.
- No ride state, dispatch, or money/wallet path is touched. No new background loop, no schema change.
- **Accepted trade-off (found by `spinr-safety-sos-reviewer`, non-blocking):** a rider with no FCM token on file (or any other permanently-unresolvable send failure) will now have the claim released and retried on every 30s tick for the rest of the ride, logging at `error` each time — previously this case was silently swallowed as a false "success" and never logged at all. Retry-forever is the correct behavior for a safety push (at-least-once beats at-most-once here), but if `error`-level logs feed a paging pipeline, one such rider produces sustained per-30s alert noise for the life of the ride. Not fixed in this PR — flagging for whoever owns paging thresholds to decide whether this specific log line needs throttling/dedup later.

## 5. User-experience effect

- **Rider-facing.** Riders on Android whose safety check-in push previously failed to render (wrong channel) or was permanently dropped on a transient failure will now actually receive it, and a real send failure will now retry instead of silently giving up. Riders in quiet-hours will now receive this push where they previously wouldn't have — flagging this explicitly since it's a real behavior change, even though it matches the treatment every other safety-tier push already gets.
- Not mid-session-surprising in a bad way: this is a reliability/correctness fix to an existing, expected notification, not new copy or a new notification type.
- No notification copy changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/safety_checkin_loop.py` | Added `priority="safety"`/`target_app="rider"` to the push call; capture return value and treat a `False` return the same as a raised exception for claim-release purposes | The actual fix |
| `backend/tests/test_safety_checkin_loop.py` | Fixed `record_push`'s mock in `test_concurrent_claim_attempts_only_one_sends` to return `True` (it previously returned `None` implicitly, which the new return-value check now correctly reads as a failure — a legitimate consequence of the fix, not a regression); added 2 new tests: claim release when the push returns `False` without raising, and correct `priority`/`target_app` kwargs | Regression coverage for both parts of the fix |

## 7. Before / after

```python
# Before
try:
    await send_push_notification(
        rider_id,
        "Safety check-in",
        "Just checking in — are you okay? Tap to confirm.",
        data={"type": "safety_checkin", "ride_id": ride_id},
    )
except Exception:
    logger.error(f"[SAFETY_CHECKIN] FCM push failed ride_id={ride_id}", exc_info=True)
    await redis_delete(_sent_key(ride_id))
    continue
```

```python
# After
try:
    sent_ok = await send_push_notification(
        rider_id,
        "Safety check-in",
        "Just checking in — are you okay? Tap to confirm.",
        data={"type": "safety_checkin", "ride_id": ride_id},
        priority="safety",
        target_app="rider",
    )
except Exception:
    sent_ok = False
    logger.error(f"[SAFETY_CHECKIN] FCM push raised for ride_id={ride_id}", exc_info=True)

if not sent_ok:
    logger.error(f"[SAFETY_CHECKIN] Push delivery failed for ride_id={ride_id}; releasing claim for retry")
    await redis_delete(_sent_key(ride_id))
    continue
```

## 8. Rollback plan

`git revert` is a full and sufficient rollback — pure application logic, no data written, no migration. No feature flag: this is a bug fix restoring the same guaranteed-delivery treatment every other safety-tier push already receives, not new user-facing behavior requiring a staged rollout.

## 9. Verification performed

- [x] `pytest backend/tests/test_safety_checkin_loop.py -v` — 23/23 passed (was 21 pre-fix; +2 new tests, +1 existing test's mock corrected to stay meaningful under the new return-value check).
- [ ] Manual repro steps followed in staging — not done; no way to trigger a real FCM delivery-to-wrong-channel or a real transient send failure from this environment.
- [x] Blast-radius grep performed: `grep -rn "safety_checkin_loop" backend/` — confirmed isolated to this file and its test file.
- [x] Reviewed against relevant CLAUDE.md conventions: mirrors the same `target_app`/`priority` pattern already established and reviewed in PR #5529; "Do not silently swallow errors" directly motivated the return-value fix.
- [x] `ruff check` / `ruff format --check` clean on both touched files.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`).
- [x] Blast radius is stated: isolated to `safety_checkin_loop.py`/its test file, confirmed by grep, not assumed.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — the opt-out/quiet-hours bypass is called out explicitly above as a deliberate, not incidental, change.

## What was NOT verified

- Not tested against a real Android device or real FCM delivery — verified only via unit tests against a mocked `send_push_notification`.
- The claim that this was actually happening in production (rider check-ins silently failing) was not independently confirmed against Sentry/production logs before this fix — the bug was found by code audit (a `spinr-safety-sos-reviewer` review of a different PR), not by observing a live incident. The fix is correct regardless, but the "how much of an active problem was this" question is unanswered.
