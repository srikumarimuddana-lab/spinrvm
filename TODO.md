# Spinr TODO List

## Critical Issues (Must Fix)

- [x] **Driver App: No push notifications** - ✅ Fixed 2026-04-11.
  FCM registration was already wired via `@shared/services/firebase`
  in `driver-app/app/_layout.tsx` but had several gaps that blocked
  end-to-end delivery. This pass added:
  - `Notifications.setNotificationHandler` at module level so
    foreground FCM messages actually render a banner + sound.
  - Android `ride-offers` channel at MAX importance with
    `bypassDnd: true`, so ride offers wake the device on Android 8+.
  - `setBackgroundMessageHandler` at module level so the JS runtime
    wakes on background/quit-state FCM messages.
  - FCM token registration gated on `isAuthInitialized && authToken`
    (previously it ran on cold start before login and silently 401'd).
  - Removed the duplicate, broken `expo-notifications` /
    `getExpoPushTokenAsync` block in `useDriverDashboard.ts` that was
    posting Expo push tokens (not FCM) to a non-existent
    `/drivers/push-token` endpoint.
  - New foreground FCM handler in `useDriverDashboard.ts` that
    bridges `new_ride_assignment` payloads into `setIncomingRide`
    (same path as the WebSocket handler).
  - Backend `/notifications/register-token` now mirrors the token
    onto `users.fcm_token` so `features.send_push_notification`
    can actually find it.
  - Test: trigger a ride offer with the driver app in the background
    on a physical Android device + physical iOS device.

- [x] **Driver App: No WebSocket reconnection** - ✅ Already
  implemented in `driver-app/hooks/useDriverDashboard.ts:360-372`.
  Exponential backoff `[1s, 2s, 5s, 10s, 30s]` with ±500ms jitter,
  `connectionState` surfaced through the dashboard hook,
  auto-reconnect on `AppState` → `active`, reset counter on successful
  reconnect, re-sends last known location on reconnect so the
  backend has a fresh position.

- [x] **Driver App: Location not batched** - ✅ Already implemented
  in `driver-app/hooks/useDriverDashboard.ts:181-271`. Posts to
  `/drivers/location-batch` every 30s. Retry-on-failure re-prepends
  failed points back to the buffer. 500-point cap prevents OOM if
  the device is offline for long periods.

## High Priority

- [x] **Backend: CORS allows all origins** - ✅ Fixed 2026-04-11.
  `core/config.py` default is now localhost-only; `core/middleware.py`
  raises `RuntimeError` if `ENV=production` and `ALLOWED_ORIGINS`
  contains `"*"`.

- [x] **Backend: Hardcoded JWT secret** - ✅ Fixed 2026-04-12.
  - Unified `backend/dependencies.py` on `settings.JWT_SECRET` from
    `core/config.py` — previously it had its own `os.environ.get`
    + hardcoded fallback, so admin JWTs and user JWTs were signed
    with DIFFERENT secrets (silent auth hazard).
  - `backend/core/middleware.init_middleware` now raises `RuntimeError`
    at startup if `ENV=production` AND `JWT_SECRET` is one of the
    known defaults (`your-strong-secret-key`,
    `spinr-dev-secret-key-NOT-FOR-PRODUCTION`) OR shorter than 32
    characters. Same fail-fast pattern as the CORS check.
  - Removed the debug log lines in `dependencies.py` that dumped
    `JWT_SECRET[:10]` on every token creation and verification error
    — logging secret material is a credential leak even for short
    prefixes, since it shortcuts offline brute-force.

- [x] **Backend: Large server.py** - ✅ Already resolved.
  `backend/server.py` is now 136 lines; route modules live under
  `backend/routes/` and are mounted in `server.py`.

- [x] **Driver App: 4-digit OTP** - ✅ Fixed 2026-04-12.
  - `backend/dependencies.generate_otp` now returns a 6-digit code
    generated with `secrets.choice` (cryptographically secure —
    previously used `random.choices` which is predictable enough
    to attack offline).
  - `backend/routes/auth.py` dev fallback bumped from `"1234"` to
    `"123456"` so it matches the new length.
  - `shared/config/spinr.config.ts` `otp.length` bumped 4 → 6.
  - `driver-app/app/otp.tsx` + `rider-app/app/otp.tsx` hardcoded
    `codeLength = 6` (no more branch on `isBackendMode`).
  - `backend/tests/test_auth.py` already asserted `len(otp) == 6`,
    so these changes actually make the test suite pass rather than
    breaking it.

- [x] **Driver App: No geofence verification** - ✅ Fixed 2026-04-12.
  The 100 m haversine check was already implemented in
  `driverStore.arriveAtPickup` at `driver-app/store/driverStore.ts:275`
  but was never invoked with location because the callsite in
  `driver-app/app/driver/index.tsx` was `() => arriveAtPickup(id)`
  with no coordinates. Fixed the callsite to pass
  `location?.coords.latitude` / `location?.coords.longitude`, so the
  check now actually runs. Drivers who tap "Arrived" while more than
  100 m from the pickup point now get a clear error message.

## Medium Priority

- [x] **Admin Dashboard: No authentication** - ✅ Fixed 2026-04-12.
  The Zustand store + login page + dashboard layout client-side
  redirect were already in place; the real gap was no Next.js
  middleware, so unauthenticated users saw dashboard HTML before
  React hydrated.
  - New `admin-dashboard/src/middleware.ts` runs at the edge and
    redirects any non-public request without the `admin_token`
    cookie to `/login?next=<original>`. Public paths allowed
    through: `/login`, `/register/*`, `/track/*` (rider share
    links), Next internals, and static assets.
  - `authStore.setToken` now dual-writes the JWT to both
    localStorage (existing) and the `admin_token` cookie (new;
    `SameSite=Lax`, 30-day max-age, `Secure` when served over
    HTTPS). `authStore.logout` clears the cookie too, so the
    sidebar logout button works end-to-end.
  - `authStore.onRehydrateStorage` re-seeds the cookie from the
    persisted token on page reload, so the cookie and localStorage
    can't drift after a refresh.
  - Login page split into a `LoginForm` client component wrapped in
    `<Suspense>` (Next.js 16 requires `useSearchParams()` to live
    under a suspense boundary) and now honors `?next=<path>` with
    sanitization (only same-origin relative paths accepted; blocks
    protocol-relative `//evil.com` and absolute URLs).

- [x] **Driver App: Hardcoded 15s timeout** - ✅ Fixed 2026-04-12.
  - New `GET /drivers/config` (`backend/routes/drivers.py`) returns
    `{ride_offer_timeout_seconds, pickup_radius_meters}` sourced
    from `app_settings` with defaults 15s / 100m and clamped to
    sane ranges (5-60s and 10-1000m respectively) so a bad admin
    input can't brick the UX.
  - `driverStore` now has `configuredCountdownSeconds` +
    `configuredPickupRadiusMeters` fields populated by a new
    `applyDriverConfig()` action. `setIncomingRide` reads the
    configured value, and `arriveAtPickup` uses the configured
    radius for the geofence check.
  - `useDriverDashboard` fetches `/drivers/config` once per
    authenticated session on mount. Failure falls back to the
    module-level defaults (15s / 100m) so a transient backend
    hiccup doesn't break the ride flow.
  - `driver/index.tsx`: timer-bar progress now divides by
    `configuredCountdownSeconds` instead of the hardcoded `/ 15`,
    so bumping the timeout in settings doesn't leave the bar stuck
    past 100%. Clamped to [0, 1] for safety.

- [ ] **Driver App: External navigation** - Leaves the app
  - File: `driver-app/app/driver/index.tsx`
  - Action: Add in-app navigation option

- [ ] **Driver App: No tip collection** - Incomplete payment flow
  - File: `driver-app/app/driver/ride-detail.tsx`
  - Action: Add tip selection UI

- [x] **Driver App: Race condition handling** - ✅ Fixed 2026-04-12,
  both sides.
  - **UI side:** When `POST /drivers/rides/{id}/accept` returns 404
    or 400 with a detail matching `/not assigned|already|no longer|
    cancelled|canceled/i`, `driverStore.acceptRide` clears
    `incomingRide` + `countdownSeconds`, resets `rideState` to
    `idle`, and surfaces "This ride was already taken by another
    driver. You'll see the next offer when it comes in."
  - **Backend side:** `POST /drivers/rides/{id}/accept` now goes
    through `db_supabase.claim_ride_atomic`, which issues a single
    conditional UPDATE: set `status=driver_accepted, driver_id=<me>`
    WHERE `id=ride_id AND status IN ('searching','driver_assigned')
    AND (driver_id IS NULL OR driver_id = <me>)`. PostgREST
    evaluates all three filters atomically, so two drivers racing
    cannot both succeed — the loser's UPDATE matches zero rows and
    the route returns 400 "Ride already accepted by another
    driver". The old read-modify-write + 70 lines of post-update
    verify shim are gone.

## Low Priority

- [x] **Driver App: No earnings export** - ✅ Fixed 2026-04-12.
  Backend `GET /drivers/earnings/export?year=<y>` already returned
  `{data, filename}` (`backend/routes/drivers.py:883`). The
  `tax-documents.tsx` Export button was wired to call the endpoint
  but threw the CSV string away and just alerted "Export Ready"
  without handing it to the driver. Now:
  - Uses React Native's built-in `Share.share({title, message})`
    to bring up the native share sheet on both platforms. Mail,
    Drive, Files, Notes all accept the CSV string.
  - Falls back to `expo-clipboard` (already a dep) with an alert
    if the share sheet is dismissed or throws.
  - No new native dependencies. Doesn't require `expo-file-system`
    / `expo-sharing`, which would need a prebuild.

- [ ] **Driver App: No dark mode** - Light theme only
  - Files: `driver-app/`
  - Action: Implement theme system

- [x] **API: No versioning** - ✅ Already done; claim was stale.
  `backend/server.py:46-72` mounts `v1_api_router` at `/api/v1`
  covering rides, drivers, documents, admin, users, addresses,
  payments, notifications, fares, promotions, disputes, webhooks,
  uploads, support, and pricing. A handful of routes are also
  double-mounted at `/api/<route>` (without the version prefix)
  for backwards compat with existing mobile clients — intentional
  and should stay until all clients are migrated.

- [ ] **Error handling** - Could be more robust
  - Files: `driver-app/`, `frontend/`
  - Action: Improve error messages

---

## Notes

- All critical and high priority items should be completed before production launch
- Medium priority items improve user experience significantly
- Low priority items are nice-to-have enhancements
