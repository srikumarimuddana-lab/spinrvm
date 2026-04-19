# Phase C — Dimensions 09–12
## Test Coverage · Error Handling · Security Headers · Compliance

---

## DIMENSION 09 — Test Coverage

### What to audit
Unit tests for store actions, WebSocket integration tests, E2E smoke + ride booking.
Verify coverage is meaningful — not just pass-through tests.

### Rider-specific risks
- `store/__tests__/rideStore.test.ts` — does it test cancellation fee logic?
- `store/__tests__/rideStore.ws.test.ts` — does it simulate driver_timeout re-dispatch?
- `store/__tests__/walletStore.test.ts` — does it test insufficient balance rejection?
- `e2e/ride-booking.spec.ts` — does it cover the full flow including rating + tip?
- No tests confirmed for: fare split, scheduled rides, promo validation, offline queue sync
- Coverage threshold: is there a Jest `--coverageThreshold` configured?

### Files to read
```
rider-app/jest.config.js
rider-app/store/__tests__/rideStore.test.ts
rider-app/store/__tests__/rideStore.ws.test.ts
rider-app/store/__tests__/walletStore.test.ts
rider-app/e2e/ride-booking.spec.ts
rider-app/e2e/smoke.spec.ts
rider-app/package.json                 ← test scripts
```

### Kick-off prompt
```
You are auditing the Spinr rider app for test coverage quality (Dimension 09).

Context:
- Framework: audit-framework/dimensions/09-test-coverage.md
- Ground rules: audit-framework/ground-rules.md
- Scope: rider-app/store/__tests__/ + rider-app/e2e/ + rider-app/jest.config.js

Your task: Work through every checklist item in dimension 09. Specific checks:
1. Is there a coverage threshold configured in jest.config.js?
2. Does rideStore.test.ts cover: createRide, cancelRide, hydrateActiveRide,
   double-booking guard, cancellation after driver_arrived?
3. Does rideStore.ws.test.ts simulate: driver_timeout (re-dispatch), ride_cancelled,
   race condition between WS update and poll response?
4. Does walletStore.test.ts cover: insufficient balance rejection, tip idempotency?
5. Are fare split, scheduled rides, promo validation, and offline queue covered anywhere?
6. Does e2e/ride-booking.spec.ts run the full flow: book → driver arriving → in-progress
   → completed → rate → tip?
7. Are there any test files that import real API endpoints (not mocked) — could cause
   flaky tests or unintended side effects?
8. What is the current test pass rate (any known failing tests)?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 09.
```

---

## DIMENSION 10 — Error Handling & Resilience

### What to audit
Every user-visible error path must show a message and a recovery action.
The offline queue must handle more than just `create_ride`. Emergency trigger must not
silently fail.

### Rider-specific risks
- API failure on `fetchEstimates()`: ride-options.tsx — does it show an error or blank screen?
- `createRide()` network failure: does rider see an error or spinner forever?
- Emergency trigger (`triggerEmergency()`): silently swallows errors — must notify user if failed
- Offline queue: only handles `create_ride` — cancellation, rating, tip during offline not queued
- Cold-start with stale AsyncStorage ride: if `/rides/active` returns 404, does app clear state?
- `completeRide()` failure: if network drops mid-completion, is the ride left as in_progress?
  Does retrying complete work (idempotent)?
- ErrorBoundary: is there one per major screen or only at root level?
- 401 during active ride: token refresh + retry must not interrupt the live ride screen

### Files to read
```
rider-app/app/ride-options.tsx          ← fetchEstimates error state
rider-app/app/driver-arriving.tsx       ← poll failure handling
rider-app/app/ride-in-progress.tsx      ← WS disconnect + poll fallback
rider-app/store/rideStore.ts            ← syncOfflineRequests, triggerEmergency
rider-app/app/_layout.tsx               ← ErrorBoundary placement
shared/api/client.ts                    ← 401 retry queue, offline detection
shared/components/ErrorBoundary.tsx
shared/components/OfflineBanner.tsx
```

### Kick-off prompt
```
You are auditing the Spinr rider app for error handling and resilience (Dimension 10).

Context:
- Framework: audit-framework/dimensions/10-error-handling.md
- Ground rules: audit-framework/ground-rules.md
- Scope: rider-app/app/ (all screens) + rider-app/store/ + shared/

Your task: Work through every checklist item in dimension 10. Specific checks:
1. fetchEstimates() failure: what does ride-options.tsx show when the API call fails?
   Empty screen or a retry-able error message?
2. createRide() network failure: does payment-confirm.tsx disable the button AND show
   an error? Or does the spinner run forever?
3. triggerEmergency() in rideStore.ts: does it swallow errors silently? The user MUST
   be notified if the emergency call fails.
4. Offline queue (syncOfflineRequests): only handles create_ride. What happens if a
   rider tries to cancel, rate, or tip while offline? Is it queued or silently dropped?
5. Cold-start stale ride: if AsyncStorage has a ride ID but /rides/active returns 404,
   does hydrateActiveRide() clear the stale state and route to home?
6. completeRide() idempotency: if the user retaps complete after a network failure, does
   the backend handle the duplicate request gracefully?
7. ErrorBoundary placement: is it only at root _layout.tsx or also on major screens
   (driver-arriving, ride-in-progress, ride-completed)?
8. 401 during active ride: does the token refresh happen silently without disrupting
   the live ride map screen?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 10.
```

---

## DIMENSION 11 — Security Headers & CORS

### What to audit
Shared backend — verify CORS is correctly scoped to rider app origin (not *).
Security headers present on all responses including error responses.

### Rider-specific risks
- CORS: rider app web build (`spinr.app`) must be in the CORS allowlist
- Deep links: `spinr.app/ride/*` and `spinr-track.app/*` — are these validated before
  routing? An attacker could craft a deep link with a fake ride_id
- Firebase App Check on rider-specific endpoints (not just driver endpoints)
- Rate limiting on rider endpoints: `/rides` POST (create), `/rides/{id}/cancel`,
  `/promo/validate` — are these rate-limited?

### Files to read
```
backend/main.py                        ← CORS middleware config
backend/routes/rides.py               ← rate limiting decorators
backend/routes/promo.py
rider-app/app.config.ts               ← deep link scheme config
rider-app/app/_layout.tsx             ← deep link handling
```

### Kick-off prompt
```
You are auditing the Spinr rider app and its backend for security headers and CORS (Dimension 11).

Context:
- Framework: audit-framework/dimensions/11-security-headers-cors.md
- Ground rules: audit-framework/ground-rules.md
- Scope: backend/main.py + backend/routes/ + rider-app/app.config.ts + _layout.tsx

Your task: Work through every checklist item in dimension 11. Specific checks:
1. CORS: is the rider web app origin (spinr.app) in the allowed origins list?
   Is wildcard (*) used anywhere?
2. Deep links: when the app handles spinr.app/ride/{id} or spinr-track.app/{token},
   is the ride_id / token validated before fetching data? Can an attacker use a crafted
   deep link to access another user's ride?
3. Rate limiting: are /rides POST and /rides/{id}/cancel decorated with SlowAPI limits?
   Is /promo/validate rate-limited to prevent code enumeration?
4. Firebase App Check: is it enforced on rider-specific backend endpoints?
5. Security headers: Content-Security-Policy, X-Frame-Options, X-Content-Type-Options
   present on all responses (including error responses)?
6. Are CORS headers present on 4xx/5xx error responses (not just 2xx)?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 11.
```

---

## DIMENSION 12 — Compliance: PII, PCI-DSS & PIPEDA

### What to audit
Rider PII exposure: what does the driver see about the rider? Rider home/work address
must not be in driver-facing responses. PIPEDA: data export and deletion.

### Rider-specific risks (rider-app focus — inverse of driver audit)
- Rider phone number: must NOT be returned to driver in ride response
- Rider home / work saved addresses: must NOT be accessible to driver after ride
- Rider payment method / card details: must NOT be in driver-facing ride response
- Rider email: must NOT be in driver-facing response
- Rating anonymity: driver cannot see their own rating until after they rate the rider
- PIPEDA: is there a "Delete my account" and "Export my data" option in account settings?
- Privacy policy: accessible from within the app (not just web)?
- Location consent: is explicit consent obtained before starting location tracking?
- Corporate ride: does corporate admin have access to rider PII? Is it scoped correctly?
- Data retention: are completed rides soft-deleted or hard-deleted?

### Files to read
```
backend/routes/rides.py               ← rider fields in driver-facing responses
backend/routes/drivers.py             ← driver gets ride — what rider fields included?
rider-app/app/(tabs)/account.tsx      ← delete account / export data options
rider-app/app/privacy-settings.tsx    ← privacy controls
rider-app/app/legal.tsx               ← privacy policy in-app
rider-app/app/(tabs)/index.tsx        ← location consent
```

### Kick-off prompt
```
You are auditing the Spinr rider app for PII protection and PIPEDA compliance (Dimension 12).

Context:
- Framework: audit-framework/dimensions/12-compliance-pii-pci.md
- Ground rules: audit-framework/ground-rules.md
  Canadian market — PIPEDA applies. Data export + deletion are legal requirements.
- Scope: backend/routes/ (rider AND driver-facing endpoints) + rider-app/app/

IMPORTANT FRAMING: For the rider audit, the compliance risk is the INVERSE of the
driver audit. The driver audit checked driver PII leaking to riders. This audit checks
RIDER PII leaking to drivers.

Your task: Work through every checklist item in dimension 12. Specific checks:
1. When a driver fetches an active ride, does the response include: rider phone number?
   rider email? rider home/work address? rider payment method? (ALL should be absent)
2. After a ride completes, can a driver still fetch the rider's saved addresses?
3. Rating: can a driver query their own rating before rating the rider?
4. PIPEDA: does account.tsx have a "Delete my account" option? An "Export my data" option?
5. Privacy policy: is it accessible in-app via legal.tsx or account settings?
6. Location consent: does index.tsx show an explicit consent explanation before requesting
   GPS permission (beyond the OS dialog)?
7. Corporate rides: does corporate admin have access to full rider PII, or only
   anonymised trip data (cost centre, amount)?
8. Soft-delete: are rides and user accounts soft-deleted (deleted_at) or hard-deleted?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 12.
```

---

## Phase C End-of-Phase Checkpoint

Before moving to Phase D, confirm:
- [ ] Findings written under TASK 09–12 in audit file
- [ ] PIPEDA compliance gaps in P1 or P2 sprint
- [ ] Any PII leak to driver is HIGH — escalated to P1
- [ ] ErrorBoundary gaps and offline queue gaps logged
- [ ] Security headers / CORS status confirmed
