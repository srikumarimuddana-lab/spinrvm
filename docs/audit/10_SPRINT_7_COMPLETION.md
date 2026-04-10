# Sprint 7 Completion Report

**Date:** 2026-04-09  
**Base branch:** `sprint6/sos-button` (`f0f34b22`)  
**Strategy:** 4 independent branches, all cut from the same base, no inter-dependencies.

---

## Issues Closed

| Issue | Branch | Status |
|-------|--------|--------|
| SEC-014 — JWT 30-day lifetime | sprint7/merge-sprint6-security | ✅ Closed |
| SEC-015 — No token refresh endpoint | sprint7/merge-sprint6-security | ✅ Closed |
| SEC-016 — OTP stored as plaintext | sprint7/merge-sprint6-security | ✅ Closed |
| SEC-017 — No address/coordinate validation | sprint7/merge-sprint6-security | ✅ Closed |
| CQ-009 — Float money arithmetic | sprint7/merge-sprint6-security | ✅ Closed |
| SEC-008 — OTP brute-force (no lockout) | sprint7/otp-lockout-redis | ✅ Closed |
| PERF-001 — No fare cache | sprint7/otp-lockout-redis | ✅ Closed |
| OPS-002 — No migration runner | sprint7/migration-runner | ✅ Closed |
| UX-001 — Cancellation policy UX | sprint7/cancellation-ux-and-admin-tests | ✅ Closed |
| CQ-010 — Admin dashboard zero tests | sprint7/cancellation-ux-and-admin-tests | ✅ Closed |

---

## Branch 1 — `sprint7/merge-sprint6-security`

Cherry-picked three unmerged sprint6 security commits onto the Sprint 7 base.

### Changes

**`backend/core/config.py`**
- `ACCESS_TOKEN_EXPIRE_MINUTES = 15` (was 10,080 min / 7 days)
- `REFRESH_TOKEN_EXPIRE_DAYS = 30`

**`backend/dependencies.py`**
- `create_jwt_token()` now uses `ACCESS_TOKEN_EXPIRE_MINUTES`
- Added `create_refresh_token(user_id) -> str` via `secrets.token_urlsafe(32)`
- Added `hash_token(raw) -> str` via SHA-256

**`backend/utils/crypto.py`** (new)
- `hash_otp(code) -> str` — SHA-256 of OTP code, so plaintext OTP never touches the DB

**`backend/schemas.py`**
- `AuthResponse` gains `refresh_token: str`, `expires_in: int`
- New `RefreshTokenRequest` schema
- `CreateRideRequest` gains `@validator` for address (3–500 chars) and lat/lng bounds

**`backend/routes/auth.py`**
- `send_otp`: stores `hash_otp(otp_code)` instead of plaintext
- `verify_otp`: queries by `hash_otp(code)`; issues access + refresh token pair
- New `POST /auth/refresh`: validates hash → checks revocation/expiry → rotates both tokens

**`shared/api/client.ts`**
- `setRefreshCallback(fn)` registered by authStore
- 401 handler calls refresh callback, deduplicates via `_refreshPromise`, retries once

**`shared/store/authStore.ts`**
- Added `refreshToken`, `tokenExpiresAt` state
- `setTokens(token, refreshToken, expiresIn)` action
- `refreshTokens() -> Promise<boolean>` action
- Refresh callback registered in `initialize()`; refresh storage purged in `logout()`

**`rider-app/app/otp.tsx` + `driver-app/app/otp.tsx`**
- Persist `refresh_token`/`expires_in` via `setTokens()` on successful verify-otp

**`backend/routes/fares.py` + `backend/routes/rides.py`**
- All fare arithmetic converted from `float` to `Decimal` via `_d()/_round()/_f()` helpers
- `float()` cast only at JSON serialization boundary

---

## Branch 2 — `sprint7/otp-lockout-redis`

### Changes

**`backend/utils/redis_client.py`** (new)
- Async Redis client using `redis.asyncio` when `REDIS_URL` is set
- Transparent in-process `dict` fallback when `REDIS_URL` is unset (zero-config dev/test)
- API: `redis_get`, `redis_set`, `redis_incr`, `redis_expire`, `redis_delete`, `redis_delete_pattern`

**`backend/core/config.py`** additions
- `REDIS_URL: str = ""`
- `OTP_MAX_FAILURES: int = 5`
- `OTP_FAILURE_WINDOW_SECONDS: int = 3600`
- `OTP_LOCKOUT_DURATION_SECONDS: int = 86400`
- `FARE_CACHE_TTL_SECONDS: int = 300`

**`backend/requirements.txt`**
- Added `redis[asyncio]>=5.0.0`

**`backend/routes/auth.py`** — OTP lockout
- `_check_otp_lockout(phone)`: raises HTTP 429 + `Retry-After` header if lockout key exists
- `_record_otp_failure(phone)`: increments sliding-window failure counter; sets lockout key at threshold
- `_clear_otp_failures(phone)`: clears both keys on successful verify
- `verify_otp`: lockout check at top; failure record on wrong code; cleared on success
- Dev bypass (`code == '1234'`) never triggers lockout counter

**`backend/routes/fares.py`** — fare result cache
- Cache key: `fares:{round(lat,2)}:{round(lng,2)}` (~1.1 km grid squares)
- TTL: 300 s (configurable via `FARE_CACHE_TTL_SECONDS`)
- Hit: return `json.loads(cached)` immediately; miss: compute → cache → return
- `invalidate_fare_cache() -> int`: flushes all `fares:*` keys (returns count deleted)

**`backend/routes/admin.py`**
- Calls `await invalidate_fare_cache()` after service-area PUT and DELETE

---

## Branch 3 — `sprint7/migration-runner`

### Changes

**`backend/migrations/00_schema_migrations_table.sql`** (new)
```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**`backend/migrations/08_complete_schema.sql`** (new, ~300 lines, idempotent)

Missing columns added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`:

| Table | Columns Added |
|-------|---------------|
| `users` | `gender`, `profile_image`, `profile_image_status`, `current_session_id` |
| `drivers` | `lat`, `lng`, `city`, `license_number`, `is_verified`, `rejection_reason`, `vehicle_year`, `vehicle_vin` |
| `service_areas` | `is_airport`, `airport_fee`, `surge_multiplier`, `free_cancel_window_seconds` |
| `rides` | `stops`, `is_scheduled`, `scheduled_time`, `corporate_account_id`, `surge_multiplier`, `driver_earnings`, `admin_earnings` |
| `saved_addresses` | `label`, `is_home`, `is_work` |

New tables (17): `refresh_tokens`, `driver_location_history`, `ride_messages`, `subscription_plans`, `driver_subscriptions`, `promotions`, `documents`, `corporate_accounts`, `corporate_rides`, `audit_logs`, `cloud_messages`, `cloud_message_audiences`, `disputes`, `staff`, `emergency_contacts`

**`backend/scripts/migrate.py`** (new, ~90 lines)
- `python backend/scripts/migrate.py [--dry-run]`
- Connects via `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` using psycopg2
- Reads `backend/migrations/*.sql` in alphanumeric order
- Skips versions already in `schema_migrations`
- Executes each in a transaction; records version on success
- Stops on first failure to prevent partial state
- `--dry-run`: prints plan without executing

---

## Branch 4 — `sprint7/cancellation-ux-and-admin-tests`

### Changes

**`rider-app/components/FreeCancelTimer.tsx`** (new)
- Props: `driverAcceptedAt`, `freeCancelWindowSeconds` (default 120), `cancellationFee` (default $3), `compact`
- `useEffect` ticks every second; derives remaining seconds from `driverAcceptedAt` timestamp
- Full layout: green banner with "Free cancellation / Xm Ys remaining" → red banner with "Cancellation fee applies / $X.XX"
- Compact layout (for dialogs): single-line text in matching colour

**`rider-app/app/ride-status.tsx`**
- Removed `const cancellationFee = Math.min(5, fare * 0.2)`
- Uses `currentRide.cancellation_fee ?? 3.0` from server
- Added `<FreeCancelTimer>` in `renderDriverAssigned()` section

**`rider-app/app/driver-arrived.tsx`**
- Removed `const cancellationFee = Math.min(5, fare * 0.2)`
- Uses `(currentRide as any)?.cancellation_fee ?? 3.0` from server
- Added `<FreeCancelTimer>` above the cancel link in the bottom sheet

**`rider-app/app/driver-arriving.tsx`**
- Moved fee derivation to component scope (was inside `handleBack()`)
- Removed `Math.min(5, fare * 0.2)` formula
- Uses `(currentRide as any)?.cancellation_fee ?? 3.0` from server
- Added `<FreeCancelTimer>` in bottom sheet (visible after driver accepted/arrived)

**`rider-app/app/ride-options.tsx`**
- Added inline `CancellationPolicyDisclosure` banner above "Confirm" button:
  "Free cancellation within 2 min of driver acceptance. A cancellation fee applies after."
- Added `cancelPolicyRow` / `cancelPolicyText` styles

**`backend/routes/rides.py`** — `GET /rides/{ride_id}`
- Added derived fields to response (no schema change):
  - `free_cancel_seconds_remaining`: seconds left in window, or `null` if driver not yet accepted
  - `free_cancel_window_seconds`: window length from `app_settings` (default 120)
  - `cancellation_fee`: fee amount from `app_settings` (default $3.00)

**Admin dashboard test infrastructure (CQ-010)**

`admin-dashboard/package.json`
- Added devDeps: `vitest`, `@vitest/ui`, `jsdom`, `@vitejs/plugin-react`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`
- Added scripts: `"test": "vitest"`, `"test:run": "vitest run"`, `"test:ui": "vitest --ui"`

`admin-dashboard/vitest.config.ts` (new)
- jsdom environment, globals true, React plugin, `@` path alias

`admin-dashboard/src/__tests__/setup.ts` (new)
- `import '@testing-library/jest-dom'`

`admin-dashboard/src/lib/__tests__/utils.test.ts` (new)
- 12 tests covering `cn()`, `formatCurrency()`, `formatDate()`

`admin-dashboard/src/__tests__/login.test.tsx` (new)
- 4 smoke tests: renders without crash, email input present, password input present, button present

`admin-dashboard/src/lib/__tests__/api.test.ts` (new)
- 7 contract tests: verifies URL, method, Authorization header for key API functions
- Uses `vi.stubGlobal('fetch', mockFetch)` and `vi.mock('@/store/authStore')`

---

## Pre-commit Hook Results

All 4 branches committed cleanly through the 5-check pre-commit suite:
1. ✅ Secrets scan
2. ✅ Forbidden files
3. ✅ PII in logs
4. ✅ Feature branch name
5. ✅ Float money arithmetic (Decimal conversions in rides/fares only)

---

## Remaining Open Issues

Sprint 7 closed 10 issues. The following remain from the original 55-issue audit:

- **P0** (production-blocking): review `docs/ISSUES.md` for current P0 status; most P0s were addressed in Sprints 1–7
- Infrastructure/ops issues beyond backend scope (e.g., CI/CD pipeline, monitoring alerts)
- Any issues deferred post-audit to Sprint 8+
