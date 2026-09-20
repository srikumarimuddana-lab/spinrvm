# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code (session), on user's explicit go-ahead |
| Surface(s) | backend |
| Domain (Sentry tag) | safety / drivers |
| PR / commit link | (branch: TBD — see commit) |
| Related issue or gap ID | Flagged earlier this session, not yet filed as a ticket |

## 1. Issue / gap identified

`backend/utils/push_retry.py`'s `_send_fcm_push()` hardcoded the Android `channel_id` to `"ride-offers"` for every non-dispatch push notification that flows through the retry queue, regardless of which app (rider or driver) the notification was actually headed to.

## 2. Root cause

`"ride-offers"` is a channel that only exists on **driver-app** (registered via `expo-notifications` in `driver-app/app/_layout.tsx:530`). **rider-app** registers a different set of channels (`"ride-updates"`, `"default"`, `"scheduled-reminders"`, `"ride-status-live"`) and has no `"ride-offers"` channel at all. Android silently drops a notification sent to a channel that doesn't exist on the receiving device — no error, no exception, nothing for the backend to observe.

The already-correct immediate-send path (`backend/features.py`'s `_build_fcm_message`, used by `_deliver_push_now`) already picks the channel by `target_app` (`"ride-updates"` for rider, `"ride-offers"` for driver — see the comment at `features.py:1356-1358`). `push_retry.py`'s `_process_row()` already reads `target_app` off the queue row but never passed it into `_send_fcm_push()`, which had no `target_app` parameter at all — so the retry path could not replicate the immediate path's (correct) behavior even though the row had the information needed to do so.

This only affects the **retry queue** (used when an immediate send fails and the push is time-critical: `priority` in `{"dispatch", "safety", "account"}`), not the immediate-send path, which was never affected.

## 3. Fix / remediation

- Added a `target_app: str | None = None` parameter to `_send_fcm_push()`.
- `_process_row()` now forwards the `target_app` it already reads from the row into `_send_fcm_push()`.
- `_send_fcm_push()` now computes `android_channel = "ride-updates" if target_app == "rider" else "ride-offers"` (identical logic and default to `features.py`'s `_build_fcm_message`) instead of hardcoding `"ride-offers"`.

Confirmed via the installed SDK's constructor and a full read of `features.py`'s existing, working implementation before writing this — mirrored its exact logic rather than inventing a new mapping.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `_send_fcm_push` has exactly one caller (`_process_row`, same file) and one other reference (its own test file) — grepped `backend/` for `_send_fcm_push` to confirm. No other code path is affected.
- **What actually changes:** only the `channel_id` value passed to `messaging.AndroidNotification` for non-dispatch pushes that go through the retry queue AND target a rider. Driver-targeted and untargeted (`target_app=None`) pushes keep the exact same `"ride-offers"` channel as before — no behavior change for those.
- **Concrete notification types that were affected** (found by grepping every `enqueue_push()` call site): `sos_confirmation` (safety, can target either rider or driver), `driver_reject`/`driver_suspend`/`driver_ban`, `driver_status_rejected`/`driver_status_suspended`/`driver_status_banned`, `stripe_payouts_blocked` (all driver-only in practice, so unaffected by this fix's behavior change, but now correctly channel-routed if that ever changes).
- **The one behavior change with real user impact:** a rider whose SOS confirmation push failed on first send and fell into the retry queue was previously being silently dropped by Android (wrong/nonexistent channel) on every retry attempt — this fix makes that push actually deliverable. This is a **bug fix that makes a safety-relevant notification reach the user it previously could not**, not a new/riskier behavior.
- No ride state, dispatch, money, or database schema is touched. No interaction with any background loop beyond `push_retry_loop` itself (unchanged cadence/logic).

## 5. User-experience effect

- **Rider-facing**, but only in the narrow case where an SOS confirmation push previously failed its first send attempt and was retried — those riders will now actually receive the push (previously silently dropped on Android). No other rider-facing behavior changes.
- Not visible mid-session in any new way — this is a delivery-reliability fix for an existing notification type, not new copy or a new notification.
- No notification copy changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/push_retry.py` | `_send_fcm_push()` gained a `target_app` parameter and uses it to pick the Android channel instead of a hardcoded value; `_process_row()` forwards `target_app` into the call | The actual fix |
| `backend/tests/test_push_retry_coverage.py` | Extended 3 existing `_process_row` tests to assert `target_app` is forwarded; added 3 new tests asserting the correct channel is selected for rider / driver / unset `target_app` | Regression coverage for the exact bug (wrong channel silently dropping notifications) |

## 7. Before / after

```python
# Before
android_cfg = messaging.AndroidConfig(
    priority="high",
    notification=None
    if is_dispatch
    else messaging.AndroidNotification(
        channel_id="ride-offers",
    ),
)
```

```python
# After
android_channel = "ride-updates" if target_app == "rider" else "ride-offers"
android_cfg = messaging.AndroidConfig(
    priority="high",
    notification=None
    if is_dispatch
    else messaging.AndroidNotification(
        channel_id=android_channel,
    ),
)
```

## 8. Rollback plan

`git revert` is a full and sufficient rollback — this is pure application logic with no data written and no migration. No feature flag needed: the change only affects which string is passed to Android's notification API, isolated to one function with one caller.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_push_retry_coverage.py -v` (39 passed, `push_retry.py` at 96% coverage in that run), plus `test_c_push_retry_atomic.py`, `test_dispatch_push_batch.py`, `test_worker_app.py`, `test_p3_push_notifications.py` (74 passed) to confirm nothing else touching push delivery regressed.
- [ ] Manual repro steps followed in staging — not done; no way to trigger a real FCM delivery-to-Android-channel-mismatch from this environment. See "what was NOT verified" below.
- [x] Blast-radius grep performed: `grep -rn "_send_fcm_push" backend/` (only caller is `_process_row`; only other reference is the test file); `grep -rn "enqueue_push(" backend/` (2 production call sites, both accounted for above).
- [x] Reviewed against relevant CLAUDE.md conventions: mirrored the existing, already-reviewed `features.py` pattern rather than inventing new logic; no money/state-machine/RLS surface touched.
- [x] `ruff check` clean on both touched files.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`).
- [x] Blast radius is stated: isolated to `_send_fcm_push`/`_process_row` in one file, confirmed by grep, not assumed.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — the one real-world behavior change (rider SOS-retry pushes now deliverable) is called out explicitly above.

## What was NOT verified

- Not tested against a real Android device or real FCM delivery — verified only via unit tests asserting the correct string is passed to the (mocked) `firebase_admin.messaging.AndroidNotification` constructor. No end-to-end path exists in this repo/environment to confirm Android actually delivers/drops based on channel_id — that behavior is documented in `features.py`'s own comment and by the client-side channel registrations found via code search, not independently re-verified against live Android behavior.
- The underlying mismatch between the plain `"ride-offers"` string this code (and `features.py`) uses and driver-app's actual Notifee-registered channel IDs (`"ride-offers-v3"`, `"ride-offers-fg-v2"`) was found during investigation but is **out of scope for this fix** — it's a pre-existing characteristic of the already-working `features.py` path that this change deliberately mirrors rather than redesigns. Flagging it here for visibility, not fixing it: `"ride-offers"` is registered as a real channel via `expo-notifications` (`driver-app/app/_layout.tsx:530`) independently of Notifee's channels, so this is not itself broken, just worth a human's awareness if driver-app's channel scheme changes in the future.
