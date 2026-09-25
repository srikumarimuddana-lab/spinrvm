# Change Impact & Risk Log: only a driver-app login signs other devices out

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (requested by repo owner: "do as Uber does") |
| Surface(s) | backend (behaviour visible in driver-app) |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/sleepy-ptolemy-yta2i1` |
| Related issue or gap ID | Follow-up to #5777 / #5780 (unexpected logouts during live testing) |

## 1. Issue / gap identified

A driver who signs into the **rider** app with the same phone number is signed out of the **driver** app and taken offline. This happens even though `driver_single_session_enabled` is `false` in production.

## 2. Root cause

Every login path (`verify_otp`, `firebase_auth_login`, `reactivate_account`, `_issue_company_email_session`) runs the same "previous session" cleanup for any user whose `users.current_session_id` changes. The cleanup tombstones the old session id, sends `session_revoked`/`session_superseded` to the user's driver and rider sockets, and calls `_offline_driver_for_logout_all`. The driver app handles that socket event by calling `logout()` (`driver-app/hooks/useDriverDashboard.ts`). No path checks which app the new login came from.

## 3. Fix / remediation

New `settings` flag `login_supersede_driver_app_only_enabled` (migration 472, default **false**, admin-writable). `routes/auth.py` `_login_supersedes_other_devices()` gates the existing cleanup in all four paths:

| Flag | New login from | Other devices |
|---|---|---|
| off / unreadable | anything | signed out (today's behaviour) |
| on | driver app (`X-App-Platform: driver`) | signed out (the "one driver device" rule) |
| on | rider app, company portal, old build with no header | untouched |
| any | a login where the driver single-session rollout is active | that rollout's own rule (unchanged) |

This matches Uber and Lyft: rider and driver apps sign in independently, and only one *driver* device at a time.

## 4. Risk & impact on existing functionality

- **Callers (blast radius):** the four login handlers above, all in `routes/auth.py`. `_cleanup_superseded_session` is only called from two of them; the other two inline the same steps. Refresh (`/auth/refresh`), logout, logout-all and the reuse cascade are untouched.
- **`current_session_id` is still overwritten on every login**, as today. Only the tombstone, socket kick and go-offline are skipped.
- **Trust in the header:** `X-App-Platform` is client-supplied. That's acceptable because the cleanup is a UX rule, not an access control; with single-session off, nothing enforces one driver device today. Forging the header can only make a login sign *more* devices out, which is today's default.
- **Company portal / reactivation:** with the flag on, these no longer sign a driver's phone out.
- **Settings read on login:** `get_app_settings()` (60s in-process cache). A failure logs an error and keeps today's behaviour.
- No interaction with the ride state machine, money, or insurance periods.

## 5. User experience effect

With the flag on:
- A driver who signs into the rider app stays signed in and online in the driver app.
- Signing into the driver app on a second phone still shows "Signed in on another phone" on the first one.
- No visible change for riders. Nothing changes until the flag is switched on.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/472_settings_login_supersede_driver_app_only.sql` | New boolean column, default false | Flag storage |
| `backend/routes/admin/settings.py` | `login_supersede_driver_app_only_enabled` on `SettingsUpdateRequest` | Flip from admin API without a DB edit |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Column added to snapshot | Drift guard maintenance |
| `backend/routes/auth.py` | `_login_supersedes_other_devices()`; gate at four cleanup sites | The behaviour change |
| `backend/tests/test_login_supersede_driver_app_only.py` | New tests (flag matrix, unreadable flag, reactivation end-to-end) | Pin both paths |

## 7. Before / after

```python
# Before (verify_otp)
if previous_session_id and str(previous_session_id) != session_id:
    await revoke_session(...); await ws_manager.kick_user(...); await _offline_driver_for_logout_all(...)
# After
if (previous_session_id and str(previous_session_id) != session_id
        and await _login_supersedes_other_devices(request, driver_session_enabled)):
    ...same three steps...
```

Scenario: a driver is online in the driver app on phone A and signs into the rider app on phone A (or B).
- **Before:** the driver app shows "Signed in on another phone", logs out, and the driver goes offline.
- **After (flag on):** both apps stay signed in, and the driver stays online.

## 8. Rollback plan

`UPDATE public.settings SET login_supersede_driver_app_only_enabled = false WHERE id = 'app_settings';` or the admin settings API. It takes effect within the 60s settings cache, with no deploy. The column can stay when the flag is off.

## 9. Verification performed

- [x] `ruff check` / `ruff format` clean; `py_compile` on changed files.
- [ ] pytest: **not run.** This container cannot reach PyPI (403 from the network policy). CI runs `tests/test_login_supersede_driver_app_only.py`, the drift/parity settings tests, and the existing supersede tests in `test_auth_remaining_endpoints.py` / `test_company_email_login.py`. Those patch `get_app_settings` to return settings without the flag, so they keep today's behaviour.
- [x] Blast-radius grep: all `previous_session_id` / `_cleanup_superseded_session` / `kick_user(... session_superseded)` sites in `routes/auth.py`.
- [x] Feature-flagged, default off.
- [x] `spinr-security-auditor` run against the diff (result in PR).

## 10. What was NOT verified

- Not exercised against staging or real devices. I confirmed that both apps send `X-App-Platform` (`setAppIdentity('rider'|'driver', …)` in each `_layout.tsx`) by reading the code, not with a live request.
- Pre-existing and not changed: every login still overwrites the shared `current_session_id`, and refresh mints access tokens with it. A logout on one device can therefore make another device's API calls fail for up to the tombstone TTL (~16 min), because a refresh re-mints the same tombstoned session id. That device is not logged out. This is a separate follow-up.
