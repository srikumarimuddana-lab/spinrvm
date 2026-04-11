# Spinr TODO List

## Critical Issues (Must Fix)

- [ ] **Driver App: No push notifications** - App won't receive rides when in background
  - File: `driver-app/app/driver/index.tsx`
  - Action: Implement Expo Notifications with FCM
  - Test: Background ride offer reception

- [ ] **Driver App: No WebSocket reconnection** - Lost rides if connection drops
  - File: `driver-app/app/driver/index.tsx`
  - Action: Add reconnection logic with exponential backoff
  - Test: Network dropout simulation

- [ ] **Driver App: Location not batched** - Inefficient individual updates
  - File: `driver-app/app/driver/index.tsx`
  - Action: Use `/api/drivers/location-batch` endpoint
  - Test: Verify batched location updates

## High Priority

- [x] **Backend: CORS allows all origins** - ✅ Fixed 2026-04-11.
  `core/config.py` default is now localhost-only; `core/middleware.py`
  raises `RuntimeError` if `ENV=production` and `ALLOWED_ORIGINS`
  contains `"*"`.

- [ ] **Backend: Hardcoded JWT secret** - Dev mode security issue
  - File: `backend/server.py`
  - Action: Ensure strong secret required in production

- [x] **Backend: Large server.py** - ✅ Already resolved.
  `backend/server.py` is now 136 lines; route modules live under
  `backend/routes/` and are mounted in `server.py`.

- [ ] **Driver App: 4-digit OTP** - Should be 6-digit for production
  - Files: `backend/server.py`, `driver-app/app/otp.tsx`
  - Action: Increase to 6-digit minimum

- [ ] **Driver App: No geofence verification** - Arrival confirmation
  - File: `driver-app/store/driverStore.ts`
  - Action: Add distance check before allowing arrival

## Medium Priority

- [ ] **Admin Dashboard: No authentication** - No visible auth implementation
  - File: `admin-dashboard/src/app/`
  - Action: Implement admin auth

- [ ] **Driver App: Hardcoded 15s timeout** - Should be configurable
  - File: `driver-app/app/driver/index.tsx`
  - Action: Make timeout configurable via API

- [ ] **Driver App: External navigation** - Leaves the app
  - File: `driver-app/app/driver/index.tsx`
  - Action: Add in-app navigation option

- [ ] **Driver App: No tip collection** - Incomplete payment flow
  - File: `driver-app/app/driver/ride-detail.tsx`
  - Action: Add tip selection UI

- [ ] **Driver App: Race condition handling** - Multiple drivers accepting
  - File: `driver-app/store/driverStore.ts`
  - Action: Handle "ride already accepted" gracefully

## Low Priority

- [ ] **Driver App: No earnings export** - Can't export for taxes
  - File: `driver-app/app/driver/earnings.tsx`
  - Action: Add CSV/PDF export

- [ ] **Driver App: No dark mode** - Light theme only
  - Files: `driver-app/`
  - Action: Implement theme system

- [ ] **API: No versioning** - No v1/v2 prefix
  - Files: `backend/server.py`
  - Action: Add API versioning

- [ ] **Error handling** - Could be more robust
  - Files: `driver-app/`, `frontend/`
  - Action: Improve error messages

---

## Notes

- All critical and high priority items should be completed before production launch
- Medium priority items improve user experience significantly
- Low priority items are nice-to-have enhancements
