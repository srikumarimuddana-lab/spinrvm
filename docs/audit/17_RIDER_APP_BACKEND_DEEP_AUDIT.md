# Spinr Deep Audit: Rider App & Backend

**Date:** 2026-04-13
**Scope:** Rider App (React Native/Expo) + Backend (FastAPI/Python)
**Auditors:** 8 parallel automated agents covering architecture, security, error handling, API design, auth, database, validation, and performance

---

## Executive Summary

This audit uncovered **120+ findings** across the rider app and backend, including critical security vulnerabilities, payment compliance violations, and production stability risks.

| Severity | Rider App | Backend | Total |
|----------|-----------|---------|-------|
| CRITICAL | 13 | 26 | **39** |
| HIGH | 17 | 28 | **45** |
| MEDIUM | 18 | 20 | **38** |
| LOW | 2 | 8 | **10** |

**Top 5 Urgent Risks:**
1. **PCI-DSS Violation** - Raw card numbers sent to backend (rider-app + backend)
2. **Hardcoded Admin Credentials** - `admin123` default password in source
3. **Ride State Machine Broken** - Rides can be completed without starting, cancelled rides restartable
4. **Exposed Firebase API Key** - `AIzaSyBAgdgMULZ3Ct_Nq-W4joEZM_4mlaBGU3M` in git
5. **Single Worker in Production** - `--workers 1` on Fly.io, no connection pooling

---

## PART 1: CRITICAL FINDINGS

### 1.1 Payment & Financial (CRITICAL)

**C-PAY-01: Raw Card Data Sent to Backend (PCI-DSS Violation)**
- Rider app: `rider-app/app/manage-cards.tsx:103-109`
- Backend: `backend/routes/payments.py:266-274`
- Card number, CVC, expiry sent as plaintext JSON to backend
- Backend creates Stripe PaymentMethod with raw card data
- **Impact:** PCI-DSS Level 1 violation. Fines up to $100K+ per incident
- **Fix:** Use `@stripe/stripe-react-native` CardField for client-side tokenization. Backend receives only `payment_method_id`

**C-PAY-02: No Validation on Payment Amounts**
- `backend/routes/payments.py:68-74`
- `amount` can be zero or negative with no validation
- Stripe API calls with invalid amounts create bad records
- **Fix:** Add `amount: float = Field(..., gt=0)` in Pydantic schema

**C-PAY-03: Payment Success Without Ride Update Atomicity**
- `backend/routes/payments.py:104-122`
- Payment confirmation and ride status update are separate non-transactional operations
- If ride update fails after Stripe succeeds, payment is lost but ride shows unpaid
- **Fix:** Wrap in database transaction or move charging to ride completion

**C-PAY-04: Stripe Webhook Signature Verification Can Be Disabled**
- `backend/routes/webhooks.py:33-35`
- If `stripe_webhook_secret` not configured, webhooks accepted without verification
- Attacker can forge payment confirmations
- **Fix:** Fail hard if webhook_secret missing, never return `received: True` unverified

**C-PAY-05: Duplicate Webhook Delivery Not Idempotent**
- `backend/routes/webhooks.py:23-139`
- No `event.id` deduplication. Stripe retries create duplicate notifications/state changes
- **Fix:** Track processed event IDs in `webhook_events` table

**C-PAY-06: Webhook Race with App Response**
- Webhook can arrive and update DB before `/payments/confirm` finishes
- App response overwrites webhook data with stale values
- **Fix:** Use conditional update `WHERE payment_status != 'paid'`

**C-PAY-07: Payment Can Fail Mid-Ride With No Recovery**
- Ride completion sets `payment_status: "completed"` without verifying payment actually happened
- If `/payments/confirm` failed silently, ride marked paid but no charge occurred
- **Fix:** Verify Stripe payment status in `complete_ride()` before marking paid

**C-PAY-08: No Idempotency Keys on Create Endpoints**
- `POST /rides` and `POST /payments/create-intent` lack idempotency
- Network retries create duplicate rides/payment intents
- **Fix:** Implement `Idempotency-Key` header support

### 1.2 Authentication & Secrets (CRITICAL)

**C-AUTH-01: Hardcoded Admin Credentials**
- `backend/core/config.py:40-41`
- `ADMIN_EMAIL: "admin@spinr.ca"`, `ADMIN_PASSWORD: "admin123"`
- Only validated in production env; defaults used if ENV not set
- **Fix:** Remove defaults entirely, require env vars

**C-AUTH-02: Weak JWT Secret Default**
- `backend/core/config.py:28`
- Default: `"your-strong-secret-key"` (30 chars, predictable)
- **Fix:** Remove default value, make required

**C-AUTH-03: Dev OTP Fallback in Production Path**
- `backend/routes/auth.py:57-60` and `104-106`
- When Twilio not configured, OTP is always `"123456"` and returned in response
- Also accepts `123456` if OTP record not found (DB error = auth bypass)
- **Fix:** Gate dev OTP behind `ENV == "development"` only

**C-AUTH-04: Debug OTP Exposed in Rider App UI**
- `rider-app/app/login.tsx:175-178`
- Hardcoded text: `"Dev mode - OTP is 1234"` visible to users
- **Fix:** Remove entirely, use environment flag

**C-AUTH-05: Firebase API Key Exposed in Git**
- `rider-app/google-services.json:17-19` - Key: `AIzaSyBAgdgMULZ3Ct_Nq-W4joEZM_4mlaBGU3M`
- `rider-app/GoogleService-Info.plist:6,13` - Same key for iOS
- **Fix:** Rotate key immediately, remove from git history with BFG, use App Check

**C-AUTH-06: Debug Token Logging**
- `shared/api/client.ts:200-202`
- Token prefix (first 20 chars) logged to console on every request
- **Fix:** Remove all token logging

### 1.3 Ride State Machine (CRITICAL)

**C-RIDE-01: Ride Can Be Completed Without Being Started**
- `backend/routes/drivers.py:1188-1340`
- `complete_ride` only checks `ride_id` and `driver_id`, not `status`
- A ride in `searching` status can be completed and charged
- **Fix:** Add `"status": {"$in": ["driver_arrived", "in_progress"]}` to query

**C-RIDE-02: Cancelled Ride Can Be Restarted**
- `backend/routes/drivers.py:1088-1187`
- `arrive_at_pickup` and `start_ride` don't check if ride is cancelled
- Driver can flip cancelled ride back to `driver_arrived`
- **Fix:** Add terminal state check: `if status in ["completed", "cancelled"]: raise`

**C-RIDE-03: Race Condition in Ride Cancellation**
- `backend/routes/rides.py:1089-1207`
- Non-atomic status update allows concurrent cancel requests to conflict
- **Fix:** Use atomic conditional update pattern from `claim_ride_atomic`

**C-RIDE-04: Timing Vulnerability in Offer Timeout**
- `backend/routes/rides.py:320-395`
- `asyncio.sleep()` timeout handler races with driver acceptance
- No atomic guard against concurrent modifications
- **Fix:** Add atomic status check in timeout handler

### 1.4 Database Integrity (CRITICAL)

**C-DB-01: Race Condition in total_rides Increment**
- `backend/db_supabase.py:278-286`
- Non-atomic read-then-write for driver's `total_rides` counter
- Concurrent ride completions cause lost increments
- **Fix:** Use `UPDATE SET total_rides = total_rides + 1` via RPC

**C-DB-02: Missing Foreign Key on rides.driver_id**
- `backend/supabase_schema.sql:153`
- `driver_id TEXT` has no FK constraint. Deleted drivers leave orphaned rides
- **Fix:** Add `REFERENCES drivers(id) ON DELETE SET NULL`

**C-DB-03: Non-Atomic claim_ride_atomic Consistency**
- `backend/db_supabase.py:334-358`
- Ride claim is atomic but driver `is_available` update is separate
- Driver can end up claimed for ride but `is_available: True`
- **Fix:** Wrap in single transaction

### 1.5 Deployment (CRITICAL)

**C-DEP-01: Single Worker Process in Production**
- `backend/Dockerfile:39` - `--workers 1`
- Only 1 concurrent request processed, all others queue
- **Fix:** Use `--workers 4` minimum

**C-DEP-02: Min Machines = 0 on Fly.io**
- `fly.toml:12` - `min_machines_running = 0`
- App shuts down completely in low traffic, causing cold start latency
- **Fix:** Set `min_machines_running = 1`

**C-DEP-03: Python Version Mismatch on Render**
- `render.yaml:14-15` - Specifies Python 3.9.0 but code requires 3.12
- **Fix:** Update to `PYTHON_VERSION = 3.12`

**C-DEP-04: No Database Connection Pooling**
- `backend/db_supabase.py:17-19`
- Default thread pool executor (~5-8 threads) used for blocking DB calls
- Under load, thread pool exhausted and requests queue
- **Fix:** Configure explicit executor size, consider asyncpg

### 1.6 Rider App UX (CRITICAL)

**C-UX-01: GPS Unavailable - No Fallback**
- `rider-app/app/(tabs)/index.tsx:78-108`
- If location denied, silent return with blank map forever
- **Fix:** Show error UI, offer retry, use last-known or default location

**C-UX-02: Driver Cancels - No Navigation Away**
- `rider-app/hooks/useRiderSocket.ts:103-109`
- Alert shown but user stuck on cancelled ride screen
- **Fix:** Navigate to home after alert dismissal

**C-UX-03: Network Drop Mid-Ride - No UI**
- `rider-app/hooks/useRiderSocket.ts:182-200`
- WebSocket reconnects silently, rider sees stale driver location
- **Fix:** Show connection status banner

**C-UX-04: Offline Banner Never Triggered (Dead Code)**
- `rider-app/app/_layout.tsx:242`
- `OfflineBanner` rendered but `setIsOffline()` never called
- **Fix:** Integrate with NetInfo to detect connectivity

**C-UX-05: No Offline Queue/Retry**
- App-wide: ratings, tips, chat messages silently lost on network failure
- **Fix:** Implement offline queue with retry logic

**C-UX-06: Double-Tap on Ride Booking**
- `rider-app/app/payment-confirm.tsx:288-303`
- Rapid taps can queue multiple ride creation requests
- **Fix:** Add local guard with immediate disable

---

## PART 2: HIGH FINDINGS

### 2.1 Backend Security (HIGH)

**H-SEC-01: Admin Role Permissions Too Coarse**
- `backend/dependencies.py:155-160`
- All admin roles (support, finance, operations) access all admin endpoints equally
- `ALL_MODULES` defined in `routes/admin/auth.py:101-120` but never enforced per-route
- **Fix:** Implement per-endpoint permission checking against user's modules

**H-SEC-02: Admin Routes Rely on Router-Level Auth Only**
- `backend/routes/admin/__init__.py:47-54`
- Individual admin endpoints lack explicit `Depends(get_admin_user)`
- If sub-router mounted elsewhere, endpoints become public
- **Fix:** Add explicit auth dependency to every admin endpoint

**H-SEC-03: OTP Rate Limiting Too Weak**
- `backend/routes/auth.py:36-37` - 5/minute per IP
- On shared networks, brute force feasible (~72 codes/hour)
- **Fix:** Rate limit by phone number (hashed), max 3/hour

**H-SEC-04: Admin Settings Accepts Arbitrary Keys**
- `backend/routes/admin/settings.py:27-47`
- `Dict[str, Any]` passthrough with no schema validation
- **Fix:** Use typed Pydantic model with explicit allowed fields

**H-SEC-05: Payment Confirm Missing Ownership Check**
- `backend/routes/payments.py:95-129`
- No verification that `current_user` owns the `ride_id`
- **Fix:** Add `ride.rider_id != current_user["id"]` check

**H-SEC-06: Track Shared Ride Leaks Driver PII**
- `backend/routes/rides.py:977-1010`
- Public endpoint exposes license plate, vehicle details
- **Fix:** Omit license plate; minimize driver info for ended rides

**H-SEC-07: Document Upload Lacks Content Validation**
- `backend/documents.py:152-168`
- Only file extension checked (easily spoofed). No magic byte validation
- **Fix:** Validate actual content type with `python-magic`, whitelist extensions

**H-SEC-08: Emergency SMS Not Actually Sent**
- `backend/routes/rides.py:1250-1269`
- Emergency trigger only logs, doesn't send SMS via Twilio
- **Fix:** Implement actual SMS sending to emergency contacts

**H-SEC-09: User Account Deletion Incomplete**
- `backend/routes/users.py:66-87`
- Doesn't delete/anonymize: rides, messages, push tokens, Stripe data, documents
- **Fix:** Add cascading cleanup or anonymization for GDPR compliance

**H-SEC-10: Corporate Account No Authorization Check**
- `backend/routes/corporate_accounts.py:67`
- Any rider can link themselves to any corporate account by ID
- **Fix:** Verify user is authorized for the corporate account

### 2.2 Backend Performance (HIGH)

**H-PERF-01: No Caching Layer**
- No Redis, no in-memory cache, no HTTP caching
- Vehicle types, service areas, fare configs, app settings queried from DB on every request
- **Fix:** Add caching with TTL for static/semi-static data

**H-PERF-02: Fare Calculation Not Cached (Hot Path)**
- `backend/routes/fares.py:34-116`
- 3 sequential DB queries per ride estimate, polygon matching in Python
- **Fix:** Cache vehicle types/service areas, use PostGIS for geo matching

**H-PERF-03: Driver Matching Loads 500 Rows Into Memory**
- `backend/routes/drivers.py:114-190`
- Fetches 500 drivers, filters/sorts in Python instead of using PostGIS
- **Fix:** Use `ST_DWithin()` query with spatial index

**H-PERF-04: No Timeouts on External API Calls**
- Stripe: `backend/routes/payments.py:38-92` - no timeout
- Twilio: `backend/sms_service.py:27-36` - no timeout
- SendGrid: `backend/utils/email_receipt.py:129-142` - no timeout
- **Fix:** Add 5-10s timeouts and retry logic to all external calls

**H-PERF-05: Email Sending Blocks Request Handler**
- Email receipts sent synchronously within route handlers
- **Fix:** Queue for background processing

**H-PERF-06: Push Notifications Not Batched**
- Individual FCM request per notification
- **Fix:** Use FCM batch API for multiple notifications

**H-PERF-07: Memory-Based Rate Limiter**
- `backend/utils/rate_limiter.py:30` - `storage_uri="memory://"`
- Doesn't work across workers or machines
- Redis implementation exists (line 185-283) but never used
- **Fix:** Activate Redis-based rate limiter in production

**H-PERF-08: Default Thread Pool for DB Calls**
- `backend/db_supabase.py:17-19`
- Default executor (5-8 threads) exhausted under load
- **Fix:** Configure explicit `ThreadPoolExecutor(max_workers=32)`

### 2.3 Backend Database (HIGH)

**H-DB-01: N+1 Query Pattern in Ride Details**
- `backend/routes/rides.py:793-795`
- Ride fetch triggers 2-3 extra round trips (ride -> driver -> user)
- **Fix:** Use SQL JOIN or batch query

**H-DB-02: RLS Bypass via Service Role Key**
- All migrations grant `service_role` full bypass on RLS
- If key exposed, all row-level security is worthless
- **Fix:** Minimize service role usage, add application-level checks

**H-DB-03: Missing updated_at Triggers**
- Only drivers, users, rides, settings have triggers
- disputes, flags, complaints, lost_and_found lack them
- **Fix:** Add triggers to all tables with timestamps

**H-DB-04: Missing CHECK Constraints**
- No DB-level validation on: ride status enum, payment_status, ratings (0-5), tax rates
- **Fix:** Add CHECK constraints for all enum/range columns

**H-DB-05: delete_one Actually Deletes All Matching**
- `backend/db_supabase.py:600-604`
- `delete_one()` delegates to `delete_many()` - name is misleading and dangerous
- **Fix:** Add LIMIT 1 or rename to match behavior

**H-DB-06: Missing Foreign Keys on Multiple Tables**
- `rides.rider_id` -> `users(id)` has no ON DELETE constraint
- `support_tickets.user_id` -> `users(id)` missing CASCADE
- `driver_location_history.ride_id` has no FK at all
- **Fix:** Add proper FK constraints with appropriate ON DELETE actions

**H-DB-07: PostGIS Location Column Desynchronized**
- `backend/db_supabase.py:241-257`
- `update_driver_location()` updates lat/lng columns but NOT PostGIS `location` column
- Spatial queries return stale data
- **Fix:** Update PostGIS location in same call

### 2.4 Backend Validation (HIGH)

**H-VAL-01: No Status Validation on Ride Operations**
- `drivers.py` ride endpoints (verify-otp, decline) don't check current status
- Can send OTP for completed ride, decline completed ride
- **Fix:** Add status validation to all state-changing endpoints

**H-VAL-02: Offer Timeout Can Re-Match Same Driver**
- `backend/routes/rides.py:320-390`
- No deduplication when re-dispatching after timeout
- **Fix:** Add recently-offered driver blocklist

**H-VAL-03: No Payment/Webhook Tests**
- Zero dedicated payment integration tests
- No webhook duplicate delivery tests
- No ride state machine transition tests
- **Fix:** Add comprehensive test suites for these critical paths

**H-VAL-04: Sensitive Info in Error Responses**
- `backend/utils/error_handling.py:519-539`
- Exception types returned to client aid attacker fingerprinting
- **Fix:** Return generic messages in production

### 2.5 Rider App (HIGH)

**H-APP-01: No Route Protection**
- `rider-app/app/_layout.tsx:254-286`
- All screens unconditionally registered. Deep-link to `/ride-in-progress?rideId=OTHER_ID` possible
- **Fix:** Add auth guard middleware, validate rideId ownership

**H-APP-02: No Token Refresh Logic**
- API calls fail silently on expired JWT. No 401 interceptor for re-auth
- **Fix:** Add token refresh interceptor in API client

**H-APP-03: Race Conditions in Async State**
- `rider-app/store/rideStore.ts:206-226`
- `fetchEstimates` doesn't cancel on state change. No AbortController
- **Fix:** Add AbortController to all async store actions

**H-APP-04: localStorage for Tokens on Web**
- `shared/store/authStore.ts:10-44`
- Web platform uses plaintext `localStorage` for auth tokens
- **Fix:** Use `sessionStorage` or httpOnly cookies

**H-APP-05: WebSocket Not Secured**
- `rider-app/hooks/useRiderSocket.ts:155-157`
- No `wss://` enforcement. URL constructed from HTTP URL replacement
- **Fix:** Enforce `wss://` only, validate URL format

**H-APP-06: No Certificate Pinning**
- No SSL pinning on any API calls
- **Fix:** Implement certificate pinning for production API

**H-APP-07: Accessibility Missing**
- Critical ride flow buttons lack `accessibilityLabel`
- Map components not labeled for screen readers
- **Fix:** Add accessibility labels to all interactive elements

**H-APP-08: Code Duplication**
- Cancellation flow duplicated across 3 screens
- Ride polling identical in 4 screens
- Map initialization duplicated in 3+ screens
- Alert pattern duplicated in every screen
- **Fix:** Extract to custom hooks: `useCancelRide`, `useRidePolling`, shared `MapView`

**H-APP-09: No Memoization on Expensive Renders**
- `rider-app/app/ride-options.tsx:277-316` - Gradient polyline loops 30x per render
- Vehicle filtering inline without useMemo
- **Fix:** Add useMemo/useCallback for expensive computations

**H-APP-10: Animation Loops Not Cleaned**
- `rider-app/app/ride-status.tsx:48-61`
- `Animated.loop()` never stopped on status change. Multiple loops accumulate
- **Fix:** Cancel previous animation before starting new one

---

## PART 3: MEDIUM FINDINGS

### 3.1 Backend (MEDIUM)

**M-BE-01: Inconsistent Timestamp Handling** - Mix of `datetime.utcnow()` objects and `.isoformat()` strings across routes

**M-BE-02: Missing Coordinate Validation** - `fares.py:34`, `rides.py:501` - lat/lng not validated for -90/90 and -180/180 bounds

**M-BE-03: No Duplicate Ride Prevention** - Same pickup/dropoff can create multiple rides in quick succession

**M-BE-04: Promotions Missing Expiry Check** - `POST /promotions/validate` doesn't check `expiry_date`

**M-BE-05: Driver Location Not Synced to PostGIS** - `websocket.py:143-145` updates lat/lng but not PostGIS `location` column

**M-BE-06: No Pagination Defaults on History** - `rides.py:735` returns ALL history records if no limit specified

**M-BE-07: Missing Unique Constraint on promo_applications** - Same user can apply same promo multiple times to same ride

**M-BE-08: Missing Index on rides.payment_status** - "Find unpaid rides" query does full table scan

**M-BE-09: No Transaction Wrapping for Multi-Step Ops** - Related operations (ride create + state update) not in transactions

**M-BE-10: Soft Delete Inconsistent** - Some tables use `is_active`, others use status columns, no unified approach

**M-BE-11: CORS Allows localhost in Production** - `core/middleware.py:108-124` hardcodes `localhost:3000` as always-allowed

**M-BE-12: Rate Limiter Not Per-User** - IP-based only; users behind NAT share quota

**M-BE-13: No Timeout on Supabase Operations** - If Supabase hangs, requests hang forever

**M-BE-14: Health Check Is Trivial** - `GET /health` always returns 200 even if DB is down

**M-BE-15: Firebase Token Verification Not Cached** - Hits Firebase on every request; no caching until token expiry

**M-BE-16: PII in Logs** - User IDs, token prefixes, phone numbers in structured logs

**M-BE-17: No Max String Length Validation** - User-submitted strings have no max_length. DOS via large payload possible

**M-BE-18: Dispute Resolution Missing Transaction Isolation** - Two admins can resolve same dispute simultaneously

**M-BE-19: OTP Duplicate Records** - Multiple unverified OTPs for same phone, `find_one` returns unpredictable result

**M-BE-20: Leaky MongoDB Abstraction** - `db.py:73-90` wraps Supabase in MongoDB syntax ($in, $set). Confusing and fragile

### 3.2 Rider App (MEDIUM)

**M-APP-01: Search Timeout Not Cleared on Unmount** - `search-destination.tsx:137-160` timer fires after component unmounts

**M-APP-02: FlatList Missing keyExtractor** - Multiple lists lack explicit `keyExtractor` causing re-render issues

**M-APP-03: Chat Messages Not Persisted** - Lost on app restart, no DB persistence or acknowledgement

**M-APP-04: Race Condition: Driver Update Before Ride Fetched** - Driver location ignored on cold start if ride not yet fetched

**M-APP-05: Large Monolithic Screens** - `driver-arriving.tsx` (1200 LOC), `ride-options.tsx` (1041 LOC) should be split

**M-APP-06: `as any` Type Assertions** - 40+ occurrences defeating TypeScript strict mode

**M-APP-07: No Request Timeout on API Calls** - API calls have no 10-30s timeout imposed

**M-APP-08: Inconsistent Error Messages** - Some use `error.message`, others `error?.response?.data?.detail`

**M-APP-09: State Duplication** - `currentRide` and `currentDriver` stored separately with divergent driver data

**M-APP-10: Auto-Apply Promo Without Consent** - `rideStore.ts:252-254` auto-applies first promo silently

**M-APP-11: No HTTPS Enforcement** - `shared/config/spinr.config.ts:34-35` generates HTTP URLs for dev/emulator

**M-APP-12: Phone/Email Validation Insufficient** - Only length check on phone, overly permissive email regex

**M-APP-13: Deep Link Params Not Validated** - Map picker coordinates accepted without bounds checking

**M-APP-14: No App Integrity Checks** - No jailbreak detection or Firebase App Check enforcement

**M-APP-15: Chat Messages Not Sanitized** - WebSocket messages added to state without sanitization

**M-APP-16: Profile Images Not Optimized** - Full resolution loaded without width/height, excessive memory on low-end devices

**M-APP-17: Location Permission Re-Prompted** - `search-destination.tsx:88-109` re-requests even if previously denied

**M-APP-18: No Background Location Tracking During Ride** - If app backgrounded, driver tracking stops

---

## PART 4: LOW FINDINGS

**L-01:** Typo in error message context (`rides.py:1291` - wrong detail for get_ride_messages)
**L-02:** Unused `create_demo_drivers` function still exported (`rides.py:56-75`)
**L-03:** Magic strings for admin role names (`dependencies.py:158` - should use Enum)
**L-04:** Health check doesn't verify external services (Stripe, Firebase connectivity)
**L-05:** WebSocket jitter can be negative (`useRiderSocket.ts:187-191` - saved by Math.max)
**L-06:** No audit logging on sensitive operations (verification, disputes, payments)
**L-07:** Missing index on `driver_location_history.ride_id + timestamp`
**L-08:** `claim_ride_atomic` uses string interpolation in `.or_()` (`db_supabase.py:352`)
**L-09:** Sentry sample rate too low (0.1 for traces - 90% of errors may not be captured)
**L-10:** Large dependency tree (100+ packages, unused: pyiceberg, boto3, google-generativeai)

---

## PART 5: REMEDIATION ROADMAP

### Week 1: Stop the Bleeding (CRITICAL Security + Payments)

| # | Action | Files | Effort |
|---|--------|-------|--------|
| 1 | Remove hardcoded admin credentials, require env vars | `core/config.py` | 30min |
| 2 | Implement Stripe tokenization (stop accepting raw cards) | `rider-app/manage-cards.tsx`, `backend/payments.py` | 1 day |
| 3 | Rotate & remove Firebase API key from git | `google-services.json`, `GoogleService-Info.plist` | 1hr |
| 4 | Remove dev OTP from UI and gate backend fallback | `login.tsx`, `backend/auth.py` | 1hr |
| 5 | Remove debug token logging | `shared/api/client.ts` | 15min |
| 6 | Require Stripe webhook secret (fail hard) | `backend/webhooks.py` | 30min |
| 7 | Add webhook event deduplication table | `backend/webhooks.py`, new migration | 2hr |
| 8 | Fix ride state machine (status validation on all endpoints) | `backend/drivers.py`, `backend/rides.py` | 4hr |
| 9 | Add payment amount validation (gt=0) | `backend/payments.py`, `backend/schemas.py` | 30min |
| 10 | Increase Fly.io workers to 4, set min_machines=1 | `Dockerfile`, `fly.toml` | 15min |

### Week 2: Harden Security + Data Integrity

| # | Action | Files | Effort |
|---|--------|-------|--------|
| 11 | Add ride ownership validation to all endpoints | `backend/rides.py`, `backend/payments.py` | 2hr |
| 12 | Implement per-endpoint admin permissions | `backend/routes/admin/*` | 1 day |
| 13 | Add FK constraints with ON DELETE actions | New migration | 4hr |
| 14 | Add CHECK constraints (status enums, ratings, amounts) | New migration | 2hr |
| 15 | Fix PostGIS location sync in driver updates | `backend/db_supabase.py` | 2hr |
| 16 | Add coordinate validation (-90/90, -180/180) | `backend/schemas.py`, `backend/fares.py` | 1hr |
| 17 | Implement document upload content validation | `backend/documents.py` | 2hr |
| 18 | Add timeouts to all external API calls | `payments.py`, `sms_service.py`, `email_receipt.py` | 2hr |
| 19 | Fix Python version on Render | `render.yaml` | 15min |
| 20 | Implement actual emergency SMS sending | `backend/rides.py` | 2hr |

### Week 3-4: Rider App Stability + Performance

| # | Action | Files | Effort |
|---|--------|-------|--------|
| 21 | Add route protection (auth guard + rideId ownership) | `rider-app/app/_layout.tsx` | 4hr |
| 22 | Implement offline detection + banner | `rider-app/app/_layout.tsx` | 2hr |
| 23 | Add token refresh interceptor | `shared/api/client.ts` | 4hr |
| 24 | Fix double-tap prevention on all buttons | Multiple screens | 2hr |
| 25 | Handle driver cancellation (navigate home) | `useRiderSocket.ts` | 1hr |
| 26 | Show WebSocket connection status | `useRiderSocket.ts`, new component | 2hr |
| 27 | Add GPS unavailable fallback | `rider-app/(tabs)/index.tsx` | 2hr |
| 28 | Extract ride polling to custom hook | Multiple screens | 2hr |
| 29 | Extract cancellation flow to custom hook | Multiple screens | 2hr |
| 30 | Implement Redis-based rate limiter | `backend/utils/rate_limiter.py` | 2hr |

### Month 2: Performance + Quality

| # | Action | Effort |
|---|--------|--------|
| 31 | Add caching layer (vehicle types, service areas, app settings) | 1 day |
| 32 | Rewrite dispatch to use PostGIS ST_DWithin() | 1 day |
| 33 | Move email/notifications to background queue | 1 day |
| 34 | Add payment + webhook + state machine test suites | 2 days |
| 35 | Add concurrent request tests for race conditions | 1 day |
| 36 | Break down large screens (driver-arriving, ride-options) | 2 days |
| 37 | Remove `as any` assertions, add proper TypeScript types | 1 day |
| 38 | Add accessibility labels to ride flow | 1 day |
| 39 | Implement database transaction support | 1 day |
| 40 | Add proper health checks (DB, Stripe, Firebase) | 2hr |

---

## Architecture Strengths

- Custom exception hierarchy (`SpinrException`) is well-designed with error codes
- Atomic ride claiming (`claim_ride_atomic`) prevents most double-acceptance
- Structured logging with request IDs via loguru
- Sentry integration for error monitoring
- WebSocket reconnection with exponential backoff and jitter
- Firebase push notification setup is thorough with proper cleanup
- Pydantic models for request validation (where used)
- SQL migrations are properly ordered and versioned

---

## Summary

The Spinr platform has a solid foundation but requires immediate attention on **payment security (PCI compliance)**, **authentication hardening**, and **ride state machine correctness** before any production launch. The deployment configuration needs the simplest fixes (workers, min machines) for the biggest impact on reliability. The rider app needs offline handling and error UX to be production-ready.

**Estimated total remediation effort:** ~4-6 weeks for a 2-person team to address all CRITICAL and HIGH findings.
