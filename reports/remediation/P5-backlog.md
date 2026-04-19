# P5 — Backlog: MEDIUM / LOW / RECOMMENDATION Items

These findings were identified in the v4 audit but were not critical enough for P0–P4.
Triage and schedule them across future sprints once P0–P4 fixes are stable in production.

**Total:** 57 MEDIUM · 18 LOW · 27 RECOMMENDATION

---

## Section 1 · Security & Auth Hardening

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 2-9 | MEDIUM | Firebase auth path does not validate token audience (driver vs. rider) — a rider token can authenticate as a driver | `backend/routes/auth.py` | 1h |
| 3-4 | MEDIUM | Dev OTP bypass "123456" lives in the main route file, not a test module — risk if env check ever fails | `backend/routes/auth.py` | 30m |
| 3-5 | MEDIUM | ADMIN_PASSWORD minimum length check is 12 chars — too permissive for an admin credential | `backend/core/middleware.py` | 30m |
| 3-6 | MEDIUM | Supabase placeholder values still present in mobile config — app ships with dummy keys | `driver-app/.env.example` | 1h |
| 2-26 | REC | Consolidate duplicate `/auth/refresh` handlers into one | `backend/routes/auth.py` | 1h |
| 2-27 | REC | Tighten `/logout` rate limit — currently 10/min, should be 3/min | `backend/routes/auth.py` | 15m |
| 2-28 | REC | Add `pip-audit` to CI pipeline for Python dependency CVE scanning | `.github/workflows/ci.yml` | 1h |
| 3-13 | REC | Rotate the Supabase service-role key — it was exposed in a previous commit | Supabase Console (manual) | 30m |
| 3-14 | REC | Add CI lint rule to block `EXPO_PUBLIC_*` keys from being committed | `.github/workflows/ci.yml` | 1h |
| 3-15 | REC | Accept `ADMIN_PASSWORD_HASH` instead of plaintext admin password in config | `backend/core/config.py` | 2h |

---

## Section 2 · Input Validation

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 4-4 | MEDIUM | OTP schema fields have no length or pattern constraints — any string accepted | `backend/schemas.py` | 1h |
| 4-5 | MEDIUM | `CreateProfileRequest` has no field constraints (min/max length, format) | `backend/schemas.py` | 1h |
| 4-6 | MEDIUM | Driver and ride model fields missing validators (plate format, VIN length, year range) | `backend/validators.py` | 2h |
| 4-7 | MEDIUM | Suspicious pattern detection (SQL injection, XSS) logs only — does not block the request | `backend/validators.py` | 1h |
| 4-8 | MEDIUM | Client-side email regex is more permissive than backend — validation can be bypassed | `driver-app/` form screens | 1h |
| 4-9 | MEDIUM | OTP code not validated at backend schema level — non-numeric or empty strings reach the handler | `backend/schemas.py` | 30m |
| 4-10 | MEDIUM | Address minimum length is 3–5 chars — too short, allows garbage data | `backend/validators.py` | 30m |
| 4-11 | LOW | Coordinates not checked against Saskatchewan/Canada service area bounding box | `backend/validators.py` | 1h |
| 4-12 | LOW | HTML stripping uses naive regex — replace with `bleach` library | `backend/validators.py` | 1h |
| 4-13 | REC | Add `max_length` to all optional string fields in Pydantic schemas | `backend/schemas.py` | 2h |
| 4-14 | REC | Require E.164 prefix explicitly — remove bare number normalisation that silently fixes bad input | `backend/validators.py` | 1h |
| 4-15 | REC | Add rate limiting to `POST /rides` — currently unlimited ride creation requests | `backend/routes/rides.py` | 30m |

---

## Section 3 · UI / UX Polish (Android & iOS)

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 5-6 | MEDIUM | OTP card in `ActiveRidePanel` not in `ScrollView` — cut off by keyboard on small screens | `driver-app/components/panels/ActiveRidePanel.tsx` | 1h |
| 5-7 | MEDIUM | Android status bar height uses hardcoded fallback of 20px instead of `StatusBar.currentHeight` | `driver-app/components/dashboard/DriverTopBar.tsx` | 30m |
| 5-8 | MEDIUM | `MapControls` bottom position (160px) hardcoded — overlaps panels on small screens | `driver-app/components/dashboard/MapControls.tsx` | 1h |
| 5-10 | MEDIUM | `DriverIdlePanel` GO button at `bottom:0` — overlaps iOS home indicator | `driver-app/components/dashboard/DriverIdlePanel.tsx` | 30m |
| 5-11 | MEDIUM | Map provider has no fallback when Google Maps API key is missing on Android | `shared/components/AppMap.tsx` | 1h |
| 5-12 | MEDIUM | `MapView` container missing `pointerEvents="box-none"` — buttons may not receive touches | `driver-app/app/driver/index.tsx` | 30m |
| 5-13 | MEDIUM | `allowFontScaling` not set on UI chrome text — system accessibility size breaks layout | Multiple component files | 2h |
| 5-14 | MEDIUM | `TripCompletedPanel` rating stars have insufficient `hitSlop` (left/right 4px only) | `driver-app/components/dashboard/TripCompletedPanel.tsx` | 30m |
| 5-15 | MEDIUM | `TripCompletedPanel` comment input has hardcoded `minHeight`/`maxHeight` — clips on small screens | `driver-app/components/dashboard/TripCompletedPanel.tsx` | 30m |
| 5-16 | MEDIUM | `EarningsLineChart`/`EarningsBarChart` empty state text not localised | `driver-app/components/charts/` | 1h |
| 5-18 | LOW | Countdown circle in `RideOfferPanel` fixed at 80×80 — looks small on Pro Max | `driver-app/components/panels/RideOfferPanel.tsx` | 30m |
| 5-23 | REC | Add `accessibilityLabel` and `accessibilityHint` to SOS button | `shared/components/SOSButton.tsx` | 30m |

---

## Section 4 · Real-Time Reliability (WebSocket & GPS)

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 6-4 | MEDIUM | No default handler for unknown WebSocket message types — messages silently dropped | `driver-app/hooks/useDriverDashboard.ts` | 1h |
| 6-5 | MEDIUM | Server error messages not surfaced to driver — connection appears healthy when it isn't | `driver-app/hooks/useDriverDashboard.ts` | 1h |
| 6-6 | MEDIUM | Redis pub/sub uses a single channel — message order not guaranteed across server instances | `backend/utils/ws_pubsub.py` | 3h |
| 6-7 | MEDIUM | Location batch upload has no max retry or cleanup on failure — can cause silent data loss | `driver-app/hooks/useDriverDashboard.ts` | 2h |
| 6-8 | MEDIUM | Chat message handler calls store method without type validation — malformed message crashes app | `driver-app/hooks/useDriverDashboard.ts` | 1h |

---

## Section 5 · Payments & Earnings

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 8-3 | MEDIUM | Payment amount not cross-checked against the ride fare — overcharge possible | `backend/routes/payments.py` | 2h |
| 8-4 | MEDIUM | Payout minimum ($10) not enforced at backend schema level — only checked in UI | `backend/schemas.py` | 30m |
| 8-5 | MEDIUM | Stripe transfer failure leaves payout record as "pending" forever — no dead-letter handling | `backend/utils/payment_retry.py` | 3h |
| 8-6 | LOW | Webhook handler has no event type allowlist — processes any Stripe event type | `backend/routes/webhooks.py` | 1h |
| 8-7 | LOW | Demo-mode payout creates a "pending" record that never resolves — pollutes earnings history | `backend/routes/payments.py` | 1h |
| 8-15 | REC | Document the $10 payout minimum in the `/drivers/balance` API response body | `backend/routes/payments.py` | 30m |

---

## Section 6 · Test Coverage

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 9-2 | MEDIUM | No test for completing a ride from `driver_assigned` state (should be rejected) | `backend/tests/test_rides.py` | 1h |
| 9-3 | MEDIUM | Full ride lifecycle integration test missing (create → accept → arrive → start → complete) | `backend/tests/test_rides.py` | 3h |
| 9-4 | MEDIUM | Auth boundary tests accept too-broad HTTP status codes (200 or 403) — should be exact | `backend/tests/test_auth.py` | 1h |
| 9-8 | MEDIUM | Zero component tests — only store logic is unit-tested; no UI component tests | `driver-app/__tests__/` | 3h |
| 9-9 | MEDIUM | Token version rotation test missing — no test verifies old tokens are rejected after rotation | `backend/tests/test_auth.py` | 2h |
| 9-10 | MEDIUM | Fixture isolation: global patches may bleed between test classes | `backend/tests/conftest.py` | 2h |
| 9-11 | MEDIUM | Async test configuration not globally enforced — some tests run synchronously by accident | `backend/tests/conftest.py` | 1h |
| 9-12 | MEDIUM | Ride state machine test gaps — transitions from cancelled/completed to other states not tested | `backend/tests/test_rides.py` | 2h |
| 9-13 | LOW | Hardcoded credentials in `conftest.py` — use environment variables instead | `backend/tests/conftest.py` | 1h |
| 9-18 | REC | Add `TESTING.md` documenting test setup, required environment variables, and how to run E2E | `TESTING.md` (new file) | 2h |

---

## Section 7 · Error Handling & Resilience

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 10-3 | MEDIUM | `validation_exception_handler` (422) missing CORS headers and `request_id` in response | `backend/utils/error_handling.py` | 1h |
| 10-4 | MEDIUM | Inconsistent error response format across four exception handlers | `backend/utils/error_handling.py` | 2h |
| 10-5 | MEDIUM | Supabase connection pool has no acquisition timeout — slow DB hangs the request forever | `backend/db_supabase.py` | 1h |
| 10-6 | MEDIUM | Supabase errors not wrapped into `SpinrException` subclasses — raw DB errors leak to client | `backend/db_supabase.py` | 2h |
| 10-7 | MEDIUM | Error boundary scope not enforced — major screens may be unprotected and crash the whole app | `driver-app/app/driver/` screens | 2h |
| 10-8 | MEDIUM | API client 401 retry has no backoff — risk of cascading refresh timeouts under load | `shared/api/client.ts` | 1h |
| 10-9 | MEDIUM | Offline queue silently drops 4xx errors after max retries — driver loses data with no feedback | `shared/api/client.ts` | 2h |
| 10-10 | LOW | Offline queue error checks use fragile string matching instead of status code comparison | `shared/api/client.ts` | 1h |
| 10-11 | LOW | Error log ring buffer entries may contain large response bodies — memory pressure risk | `shared/api/client.ts` | 1h |
| 10-19 | REC | Add per-request timeout override for file uploads (current 15s global is too short) | `shared/api/client.ts` | 1h |
| 10-20 | REC | Map Pydantic validation error types to human-readable messages in error responses | `backend/utils/error_handling.py` | 2h |

---

## Section 8 · Compliance & PII

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 12-7 | MEDIUM | `stripe_account_id` and `bank_account` returned in driver self-endpoint — strip these fields | `backend/routes/drivers.py` | 1h |
| 12-8 | MEDIUM | Original upload filename returned in API response — leaks driver's local file name (PII) | `backend/documents.py` | 30m |
| 12-9 | MEDIUM | WebP magic byte check accepts non-WebP RIFF files (e.g. WAV audio) — bypass possible | `backend/documents.py` | 1h |
| 12-11 | LOW | File extension not validated against allowlist — only MIME type is checked | `backend/documents.py` | 30m |
| 12-12 | LOW | FCM tokens stored in plaintext — allows phishing notification attacks if DB is read | `backend/migrations/` | 1 day |

---

## Section 9 · Notifications

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 13-3 | MEDIUM | All notification payloads missing `deeplink` field — tap does nothing even after P3-9 fix | `backend/routes/notifications.py` | 2h |
| 13-6 | MEDIUM | Foreground notification handler missing `subscription_expiring` and `document_expiry_warning` cases | `shared/services/firebase.ts` | 1h |
| 13-7 | MEDIUM | No OTP push flow — drivers who dismiss the app miss the OTP entirely | `backend/routes/auth.py` | 3h |
| 13-8 | LOW | `ride_auto_cancelled` vs. `ride_cancelled` naming inconsistency breaks app routing | `backend/routes/` + `driver-app/` | 1h |
| 13-9 | LOW | Payment retry notifications missing intermediate steps (e.g. "retry 2 of 3 in progress") | `backend/utils/payment_retry.py` | 2h |
| 13-10 | LOW | Driver not notified when their ride payment fails — they don't know the rider didn't pay | `backend/routes/webhooks.py` | 1h |
| 13-17 | REC | Add Help & Support section to Settings screen linking to FAQ and contact options | `driver-app/app/driver/settings.tsx` | 1h |

---

## Section 10 · Performance & Scalability

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 14-8 | MEDIUM | `FlatList` missing `getItemLayout`, `initialNumToRender`, `windowSize` — poor scroll performance on long lists | Multiple screens | 2h |
| 14-11 | MEDIUM | Profile avatar uses plain `Image` with no caching — re-downloads on every render | `driver-app/` profile components | 1h |
| 14-15 | MEDIUM | No composite index on `rides(driver_id, created_at)` — history queries do full table scans | `backend/migrations/` | 30m |
| 14-16 | MEDIUM | Driver estimate query fetches max 500 drivers — misses available drivers in dense areas | `backend/services/dispatch_service.py` | 2h |
| 14-17 | MEDIUM | Rider history endpoint has no pagination — returns all rides in one response | `backend/routes/rides.py` | 1h |
| 14-19 | MEDIUM | 15 `console.log` statements in production dashboard hook — log noise and minor perf cost | `driver-app/hooks/useDriverDashboard.ts` | 30m |
| 14-2 | MEDIUM | Metro `maxWorkers = 2` — slows local dev builds unnecessarily | `driver-app/metro.config.js` | 15m |
| 14-9 | LOW | `removeClippedSubviews` not enabled on `FlatList`s — wastes memory on long lists | Multiple screens | 1h |
| 14-5 | REC | Audit bundle size — lazy-load heavy modules (charts, maps) to reduce initial load time | `driver-app/` | 1 day |
| 14-6 | REC | Monitor New Architecture (Fabric/JSI) for crashes after first production build | Firebase Crashlytics | Ongoing |
| 14-13 | REC | Add Blurhash placeholders for profile images on slow networks | `driver-app/` profile components | 2h |

---

## Section 11 · Dispatch & Ride Logic

| ID | Severity | Finding | File | Effort |
|----|----------|---------|------|--------|
| 7-6 | MEDIUM | Driver going offline between dispatch read and ride claim has no guard — ghost assignments possible | `backend/services/dispatch_service.py` | 2h |
| 7-9 | LOW | Cancellation fee payout to driver is commented out — drivers never receive cancellation fees | `backend/routes/rides.py` | 2h |
| 7-10 | LOW | Trip earnings endpoint has no limit cap or offset bounds — can return unbounded data | `backend/routes/rides.py` | 1h |
| 7-13 | REC | Consolidate inline dispatch algorithm with `DispatchService` — logic split across two files | `backend/services/dispatch_service.py` | 3h |
| 7-14 | REC | Add cancellation window policy UI — drivers and riders need to see the cancellation rules | `driver-app/app/driver/` | 2h |

---

## Effort Summary

| Section | MEDIUM | LOW | REC | Est. Days |
|---------|--------|-----|-----|-----------|
| 1 · Security & Auth | 4 | 0 | 6 | 2–3 |
| 2 · Input Validation | 7 | 2 | 3 | 2–3 |
| 3 · UI/UX Polish | 10 | 1 | 1 | 3–4 |
| 4 · Real-Time | 5 | 0 | 0 | 2 |
| 5 · Payments | 3 | 2 | 1 | 1–2 |
| 6 · Test Coverage | 7 | 1 | 1 | 3–4 |
| 7 · Error Handling | 7 | 2 | 2 | 2–3 |
| 8 · Compliance & PII | 3 | 2 | 0 | 2–3 |
| 9 · Notifications | 3 | 3 | 1 | 2 |
| 10 · Performance | 7 | 1 | 3 | 3–4 |
| 11 · Dispatch | 1 | 3 | 2 | 2 |
| **Total** | **57** | **17** | **20** | **~24–32 days** |

---

## Suggested Sprint Order

1. **Sprint 1** — Sections 1 + 2 (Security hardening + validation gaps — highest risk)
2. **Sprint 2** — Sections 6 + 7 (Test coverage + error handling — quality foundation)
3. **Sprint 3** — Sections 3 + 4 (UI polish + real-time reliability — beta feedback driven)
4. **Sprint 4** — Sections 5 + 8 + 9 (Payments + compliance + notifications)
5. **Sprint 5** — Sections 10 + 11 (Performance + dispatch — optimisation last)
