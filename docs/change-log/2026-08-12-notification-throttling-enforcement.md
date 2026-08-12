# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides (push delivery is a cross-cutting utility, but the highest-volume callers are dispatch/safety) |
| PR / commit link | (branch `claude/rideshare-app-analysis-blx3pn`, follow-up to merged #3719) |
| Related issue or gap ID | subtask 3/5 of the notification-throttling feature |

## 1. Issue / gap identified

Spinr has no quiet-hours or daily-frequency-cap enforcement for non-critical push/SMS/email — a user can be notified at any hour, any number of times, with no fatigue-prevention control.

## 2. Root cause

The feature was never built. `notification_preferences` had a boolean `push_enabled`/channel opt-out, but no time-window or count-based throttle existed anywhere in the send path.

## 3. Fix / remediation

Wired the previously-inert `utils/notification_throttle.py` (subtask 2, unwired) into `features.py::send_push_notification`. When `notification_throttling_enabled` is on (defaults **off**), a non-time-critical push is suppressed if it falls inside the configured quiet-hours window or the sending user has already hit the daily cap. `dispatch`/`safety`/`account` priority pushes bypass this entirely — the same `time_critical` short-circuit that already bypasses the existing `push_enabled` opt-out.

## 4. Risk & impact on existing functionality

- **Blast radius: cross-cutting.** `send_push_notification` has ~35 real call sites across `services/dispatch_service.py`, `routes/rides/*`, `utils/safety_checkin_loop.py`, `utils/scheduled_rides.py`, `utils/payment_retry.py`, `services/cancellation_service.py`, `services/corporate_*`, `routes/admin/*`, `utils/marketing_push.py`, and more — enumerated via grep during planning, not guessed (101 files reference the symbol including tests).
- **Who else reads/writes the same code path:** every caller listed above shares this one function. No caller was modified — the new check sits inside the existing `if not time_critical:` preference block, so any caller that already tolerates the `push_enabled` opt-out suppressing a send tolerates this the same way.
- **Regression risk if the flag is ever flipped on without review:** a caller that assumed every "normal"-priority push always reaches the device could see suppressed sends during quiet hours or past the cap. Mitigated by: (a) the flag defaults `false` — zero behavior change at merge, (b) `dispatch`/`safety`/`account` are structurally exempt, so nothing time-critical is ever affected regardless of flag state.
- **Background loops:** none of the 18 startup loops call `send_push_notification` directly with a priority this change treats differently — loops that do send (`safety_checkin_loop.py`, `scheduled_rides.py`, `payment_retry.py`, `document_expiry.py`) mostly use `normal`/`account` priority pushes for reminders, which is the intended throttling target once enabled, not an unintended side effect.
- **Not interacting with:** ride state machine, money/wallet deltas, WS events, RLS.

## 5. User-experience effect

- **Who sees a difference:** riders and drivers, only once an admin explicitly turns `notification_throttling_enabled` on in staging/production — not at merge time.
- **Visible mid-session?** Only in the sense that a suppressed push means one fewer notification arrives; the in-app inbox row is still written regardless (existing `_record_inbox_notification` behavior, unchanged), so nothing is lost from the Notifications page — only the push/SMS/email delivery is gated.
- **Copy/notification change:** none — no new notification text, only a delivery-timing/frequency gate on existing notification types.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/features.py` | Added a throttle check inside `send_push_notification`'s existing `if not time_critical:` preference block, after the `ride_updates` opt-out check | This is the single choke point every push flows through — same reasoning the existing `push_enabled`/`ride_updates` checks already use |
| `backend/tests/test_p3_push_notifications.py` | Added `TestNotificationThrottlingWiring` (8 tests): flag-off bypass, throttled-suppresses, not-throttled-delivers, dispatch/safety/account bypass (3 tests), settings-lookup-failure fail-open | Direct coverage of the new wiring, patching the source modules (`backend.settings_loader`, `backend.utils.notification_throttle`) since both are local imports inside the function |

## 7. Before / after

```python
# Before (inside `if not time_critical:` block, after the ride_updates check)
            notif_type = (data or {}).get("type")
            if notif_type in _RIDE_UPDATE_PUSH_TYPES and pref_rows and pref_rows[0].get("ride_updates") is False:
                logger.info(...)
                return False
        except Exception:
            logger.opt(exception=True).error(...)
```

```python
# After
            notif_type = (data or {}).get("type")
            if notif_type in _RIDE_UPDATE_PUSH_TYPES and pref_rows and pref_rows[0].get("ride_updates") is False:
                logger.info(...)
                return False

            # Global quiet-hours + daily-cap throttling, gated behind
            # notification_throttling_enabled (defaults False).
            settings = await get_app_settings()
            if settings.get("notification_throttling_enabled"):
                throttled = await should_throttle(
                    user_id,
                    settings.get("notification_quiet_hours_start") or "22:00",
                    settings.get("notification_quiet_hours_end") or "07:00",
                    int(settings.get("notification_daily_cap") or 0),
                )
                if throttled:
                    logger.info(...)
                    return False
        except Exception:
            logger.opt(exception=True).error(...)
```

## 8. Rollback plan

- **Feature flag to flip off:** `notification_throttling_enabled` — a single admin-settings PUT, effective within the 60s settings-cache TTL, no redeploy needed. This is the primary rollback path.
- The flag already defaults `false`, so this commit alone (before an admin ever turns it on) requires no rollback action — the code path is present but dormant.
- No data-level remediation needed in any scenario: this feature never mutates ride state, money, or wallet data; the worst case of a bad rollout is a suppressed notification, not a corrupted record. `git revert` is also safe here (unlike money/state changes) since nothing this touches is applied to live data beyond the in-memory Redis daily-cap counters, which self-expire in 24h regardless.

## 9. Verification performed

- [x] Automated tests run — unit: `test_notification_throttle.py` (18/18, from subtask 2), `test_p3_push_notifications.py` (40/40 including 8 new throttle-wiring tests); targeted regression sweep on the highest-risk real callers: `test_offer_timeout.py`, `test_dispatch_notify_loop_branches.py`, `test_p2_sos.py`, `test_ride_state_machine.py`, `test_features.py` (84/84 combined). Full `pytest -m "not slow"` suite run in background for broader confirmation.
- [ ] Manual repro in staging — not performed this session (no live Supabase/staging access); flagging as **not verified against a real deployed environment**, only against `mock_supabase_client`-backed unit tests and direct patching.
- [x] Blast-radius grep performed — `grep -rl "send_push_notification("` across `backend/` (101 files including tests, ~35 real non-test call sites), enumerated during the original planning pass, not re-guessed for this commit.
- [x] Reviewed against relevant `CLAUDE.md` conventions — dual-import pattern (both `get_app_settings`/`should_throttle` imports use try/relative-then-bare-except), "don't silently swallow errors" (the settings-lookup failure is caught by the existing broad `except Exception` at error level with `logger.opt(exception=True).error`, matching the pre-existing fail-open contract for this whole block — not a new silent-swallow, an extension of an already-reviewed one), no money/state-machine/RLS surface touched.
- [x] Feature-flagged — `notification_throttling_enabled`, defaults `false`. Ships dark; verify in staging before flipping on.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — flip one boolean setting field, no deploy, no data cleanup.
- [x] Blast radius is stated, not assumed — 101 files / ~35 real call sites, enumerated by grep.
- [x] No silent behavior change to an already-shipped flow — the flag defaults off, so no currently-shipped notification behavior changes until an admin opts in.
