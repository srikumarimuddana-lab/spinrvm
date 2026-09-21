# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-17 |
| Author | agent |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (uncommitted at write time) |
| Related issue or gap ID | ENV=production enabled App Check before DeviceCheck/Play Integrity were registered |

## 1. Issue / gap identified

After `ENV=production`, rider/driver apps could verify OTP but then saw empty profile/rides and had to re-login on minimize. Fly logs showed `/auth/me`, `/auth/refresh`, and rides returning 401 `App Check token required`.

## 2. Root cause

`FirebaseAppCheckMiddleware` was hard-wired to `enforcement_enabled=(ENV==production)`. Production builds and local/dev clients were not minting a valid `X-Firebase-AppCheck` header (Firebase Console DeviceCheck / Play Integrity not registered). JWT auth never ran.

## 3. Fix / remediation

Added `APP_CHECK_ENFORCEMENT` env override (`off`/`false`/`0`/`no` disables; `on`/`true`/`1`/`yes` forces on; blank follows `ENV`). Production secret guards stay on. Live value set to `off` until Firebase App Check is configured.

Alternative considered: revert `ENV=development`. Rejected because that also skips production secret-strength / region / Redis guards. A dedicated switch is smaller blast radius.

## 4. Risk & impact on existing functionality

- Blast radius: single middleware (`FirebaseAppCheckMiddleware`). Every `/api/*` path not in `_APP_CHECK_EXEMPT_PREFIXES` currently 401s without a token; with the switch off those requests proceed to JWT/OTP/rate-limit as they did under `ENV=development`.
- Does not change ride state machine, wallet deltas, Stripe, FCM send, WebSockets (already exempt), admin/portal (already exempt).
- Security: unofficial clients with a stolen JWT can call the API until enforcement is turned back on. OTP lockout, JWT expiry, and rate limits still apply.
- Push: `POST /notifications/register-token` is App-Check-gated; turning off unblocks token registration rather than changing FCM send.

## 5. User-experience effect

- Rider / driver: profile, rides, refresh-on-resume should load again on clients that cannot mint App Check tokens.
- Visible mid-session: yes — the next `/auth/me` or `/auth/refresh` after the rolling deploy will succeed instead of 401, so a user who is stuck on an empty home can pull-to-refresh or reopen the app without a new OTP if their refresh token is still valid.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/config.py` | `APP_CHECK_ENFORCEMENT` + `app_check_enforced()` | Independent of ENV |
| `backend/core/middleware.py` | Middleware uses `settings.app_check_enforced()` | Honor the kill switch |
| `backend/tests/test_core_config_coverage.py` | Parser cases | Regression for on/off/blank |

## 7. Before / after

```
# Before
app.add_middleware(FirebaseAppCheckMiddleware, enforcement_enabled=is_production)
```

```
# After
app.add_middleware(FirebaseAppCheckMiddleware, enforcement_enabled=settings.app_check_enforced())
```

## 8. Rollback plan

Without a second image deploy: `fly secrets unset APP_CHECK_ENFORCEMENT -a spinr-backend-yyz` (and the Railway equivalent). Blank follows `ENV=production` and enforcement turns back on. Do that only after DeviceCheck/Play Integrity are registered, or the 401s return.

## 9. Verification performed

- [x] Unit: `TestAppCheckEnforced` in `test_core_config_coverage.py` (6 passed)
- [x] Blast-radius grep: `FirebaseAppCheckMiddleware`, `enforcement_enabled=is_production`, `APP_CHECK`
- [x] Live: `GET https://api-spinr.spinr.ca/api/v1/auth/me` without App Check header returned `No authorization token provided` (401 JWT), not `App Check token required`. `/health` 200.
- [x] Production image: Fly rolling deploy of backend (`spinr-backend-yyz`), `printenv APP_CHECK_ENFORCEMENT` = `off` on a running machine. ENV left at production.

## 10. What was NOT verified

- Firebase Console App Check registration (still not done — that is why this is off).
- Store / TestFlight / Play binaries minting real App Check tokens.
- Visual regression: backend-only, no UI.
- Railway image until that service is redeployed with this code; Fly is primary.
