# Change Impact & Risk Log: per-login sessions; only a driver-app login signs other devices out

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (requested by repo owner: "do as Uber does") |
| Surface(s) | backend (behaviour visible in driver-app and rider-app) |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/sleepy-ptolemy-yta2i1` |
| Related issue or gap ID | Follow-up to #5777 / #5780 (unexpected logouts during live testing) |

## 1. Issue / gap identified

- **Driver app signed out by the rider app.** A driver who signs into the rider app with the same phone number is signed out of the driver app and taken offline, even though `driver_single_session_enabled` is `false` in production.
- **One phone's logout breaks another.** A logout on one device can make another device's API calls, socket and GPS uploads fail for up to ~16 minutes.
- **Rider-app logout pauses the driver.** A rider-app logout by a driver can stop that driver's ride requests.

## 2. Root cause

There is one shared session identity per account.
- **Logins:** every login overwrites `users.current_session_id`. Every login also tombstones the previous value, kicks all of the user's sockets (`session_superseded`, which the driver app answers with `logout()`), and takes the driver offline.
- **Refresh:** `/auth/refresh` mints each new access token from that shared column (`_build_refresh_response`). So every device's tokens converge on whichever login happened last.
- **Logout:** it tombstones the token's session id when it equals the shared column, which can be another device's.
- **Availability:** driver availability v2 (`driver_availability_service.py`) reads "token session ≠ current_session_id" as `SESSION_SUPERSEDED`.

## 3. Fix / remediation

All of this sits behind new `settings.login_supersede_driver_app_only_enabled` (migration 472, default **false**, admin-writable). The same migration adds nullable `refresh_tokens.session_id`, so the flag can only be enabled once the column exists.

With the flag on:
- **Who owns the account's session** (`_login_session_policy`): only a login from the driver app (`X-App-Platform: driver`) writes `users.current_session_id` and signs the account's other devices out. So does a login the driver single-session rollout owns. Rider-app, company-portal and header-less logins do neither. `current_session_id` becomes "the driver's session".
- **Per-login session ids:** every login stores its own session id on its refresh-token row. Rotation carries it forward, and refreshed access tokens use it (`_chain_session_id_for_rotation`, `_build_refresh_response(session_id=...)`; the X8 recover path uses the successor's id).
- **Legacy chains** adopt an id on their first rotation. A rider-app device gets a fresh id. Any other device keeps `current_session_id`, the id its tokens already carry, so a driver's availability controller binding survives mid-shift.
- **Logout** also tombstones the token's own session id when the refresh token being signed out carries that same id. Holding it proves the device owns that chain. The old "equals `current_session_id`" rule stays.

With the flag off, every call is exactly as before; the `session_id` kwarg is omitted.

This matches Uber and Lyft: rider and driver apps sign in independently, and only one driver device at a time.

## 4. Risk & impact on existing functionality

- **Callers (blast radius):** `routes/auth.py` login handlers (`verify_otp` existing and new user, `firebase_auth_login`, `reactivate_account`, `_issue_company_email_session`), `refresh_access_token` (rotate + X8 recover), `logout`, and `utils/refresh_tokens.issue_refresh_token` / new `refresh_token_session_id`. Admin auth calls `issue_refresh_token` without `session_id`, so it is unchanged.
- **Readers of `users.current_session_id`:** `should_tombstone` (logout), driver availability snapshot (`driver_availability_service.py`), the `begin_driver_session` RPC (single-session rollout, off), and the supersede cleanup. With the flag on it tracks the driver session, which is what availability expects.
- **Readers of the token `session_id` claim:** `is_session_revoked` (HTTP opt-in paths, WS handshake + heartbeat, driver GPS ingest), `driver_session_end_service` (skips a logout whose session isn't the controller), `ride_complete.py`. Each device now carries a stable id of its own.
- **Mutually exclusive with `driver_single_session_enabled`.** That rollout's `begin_driver_session` RPC runs on every driver-account login and revokes the account's rider and driver refresh tokens. If both flags are on, this flag is ignored (logged at error) and single-session wins, enforced in `_driver_app_only_sessions_enabled`. It is `false` in production.
- **Transition when the flag is switched on (accepted, bounded; needs sign-off).** A legacy driver chain (no `session_id`) adopts `users.current_session_id` on its first rotation. That is the id its tokens already carry, so availability binding survives. If the last login before the flip was a rider-app login, that id is shared with that rider device's in-flight access token. Until that token expires (≤ access-token TTL, ~15 min), a logout from it can still tombstone the shared id: exactly today's behaviour, and never worse than flag-off. After that the rider chain rotates to a fresh id and the collision is gone. Forcing every device to re-login at the flip was the alternative, rejected as more disruptive than the ≤15-minute window it removes.
- **Trust in `X-App-Platform` (security trade-off; needs sign-off).** It is client-supplied. It decides only whether a login takes over the driver session and signs others out; session ids are always server-generated. Today any new login kicks every other device, which is an unintended takeover signal. With the flag on, an attacker who already holds valid credentials can log in without displacing the real driver by omitting the header or sending `rider`. That is inherent to independent rider/driver sessions (Uber's model). New-device alerts (`_alert_if_new_device`) remain the takeover signal and should be extended to driver logins as a follow-up. Logout's own-chain rule also checks that the presented refresh token belongs to the logged-in user.
- **Extra reads:** one settings read per login/refresh/logout (60s in-process cache). One `refresh_tokens` lookup per logout when the flag is on.
- No interaction with ride state transitions, money, or insurance-period rows.

## 5. User experience effect

With the flag on:
- A driver who signs into the rider app stays signed in and online in the driver app.
- Signing into the driver app on a second phone still shows "Signed in on another phone" on the first.
- Logging out on one phone no longer breaks the other phone's app.

Nothing changes until the flag is switched on. There is no copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/472_settings_login_supersede_driver_app_only.sql` | Flag column + nullable `refresh_tokens.session_id` | Flag + per-chain session storage, shipped together |
| `backend/routes/admin/settings.py` | Field on `SettingsUpdateRequest` | Flip via admin API |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Snapshot entry | Drift guard maintenance |
| `backend/routes/auth.py` | Session policy helpers; four login paths; refresh rotate/recover; logout own-session tombstone | The behaviour change |
| `backend/utils/refresh_tokens.py` | `issue_refresh_token(session_id=)`, `refresh_token_session_id()` | Store/read the chain's session id |
| `backend/tests/test_login_supersede_driver_app_only.py` | Policy matrix, rotation id rules, refresh mint, reactivation ownership, logout rules, storage | Pin each rule |

## 7. Before / after

Scenario (flag on): a driver is online on phone A (driver session D). The same person signs into the rider app (session R), later logs out of the rider app, and then signs into the driver app on phone B.

- **Before (today):**
  1. The rider login overwrites `current_session_id` = R, tombstones D, and kicks A. The driver app logs out and the driver goes offline.
- **After, with the flag on:**
  1. The rider login keeps `current_session_id` = D. The R chain stores R. A is untouched and stays online.
  2. A refreshes. The D chain carries D, and the new access token has D. Availability still sees D = current.
  3. The rider logs out. Its refresh row proves R, so only R is tombstoned. A is unaffected.
  4. The phone-B driver login sets `current_session_id` = B and tombstones D. A shows "Signed in on another phone".

```python
# Before: _build_refresh_response
session_id = user.get("current_session_id") or ""
# After
session_id = session_id or user.get("current_session_id") or ""   # chain's own id when the flag is on
```

## 8. Rollback plan

`UPDATE public.settings SET login_supersede_driver_app_only_enabled = false WHERE id = 'app_settings';` or the admin settings API. It takes effect within the 60s settings cache, with no deploy. Flag-off code never reads `refresh_tokens.session_id`, so rows written while it was on are ignored; tokens fall back to `current_session_id` at their next refresh.

## 9. Verification performed

- [x] `ruff check` / `ruff format`; `py_compile`.
- [x] Ran `test_auth_session_id_integrity.py`'s AST guard logic locally against the edited `auth.py`: 3 session-write handlers, all raise.
- [ ] pytest suites: **not run.** This container cannot reach PyPI (403 from the network policy). CI runs the new test file plus the existing auth, refresh (`test_p1_token_refresh.py`, `test_refresh_successor_route.py`, `test_auth_session_id_integrity.py`), logout, reactivation and company-email suites. Those existing tests do not enable the flag and see byte-identical `issue_refresh_token` calls.
- [x] Blast-radius grep: `current_session_id`, `session_id` claim readers, `issue_refresh_token` callers, the `session:{user_id}` Redis key (written and deleted only, never validated).
- [x] Feature-flagged, default off.
- [x] `spinr-security-auditor`: the first version had a blocker and was redesigned. This version: steady state confirmed correct. The two rollout findings were addressed: single-session mutual exclusion is now enforced in code, and the flip-time transition is documented above as a bounded, accepted risk. The header trade-off is documented above. An ownership check on the logout lookup was added.

## 10. What was NOT verified

- No staging or real-device run.
- I did not run the real-Postgres availability-v2 tests (`driver-availability-db.yml`); the driver-session interaction was reasoned from `driver_availability_service.py`.
- Background driver refreshes send no `X-App-Platform`. On a legacy chain they adopt `current_session_id`, the same id the driver's tokens already carry.
- The 12 remaining real-Postgres failures from #5770 (controller rebind on re-login) are a separate, not-yet-built dispatch feature. They are not addressed here.
