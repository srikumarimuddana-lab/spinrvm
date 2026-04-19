# Phase A — Dimensions 01–04
## Feature Completeness · Authentication · Encryption · Input Validation

**Run this phase first.** These four dimensions catch the most critical structural gaps
before spending time on UI/UX or performance. Each dimension is a separate agent call.

---

## DIMENSION 01 — Feature Completeness

### What to audit
Every planned screen must exist, be navigable, and have a defined success + error state.
The rider app has 36 screens — verify none are stubs or dead ends.

### Rider-specific risks
- `rate-ride.tsx` exists but may not be wired into `ride-completed.tsx` completion flow
- `become-driver.tsx` exists but onboarding path to driver-app is unclear
- `fare-split.tsx` has full store actions but no clear entry point from any screen
- `scheduled-rides.tsx` exists but activity tab does not list scheduled rides
- `chat-driver.tsx` uses polling (no WebSocket delivery) — messages may be missed
- `corporate ride` flow (`corporate_rider.py` backend) — no UI entry point confirmed
- PIPEDA requires: data export screen + account deletion screen (not confirmed present)
- Legal / Terms screen required for App Store submission

### Files to read
```
rider-app/app/_layout.tsx               ← auth guard + navigation root
rider-app/app/(tabs)/index.tsx          ← home screen
rider-app/app/ride-completed.tsx        ← does it call rate-ride flow?
rider-app/app/rate-ride.tsx             ← exists? wired?
rider-app/app/fare-split.tsx            ← entry point?
rider-app/app/become-driver.tsx         ← complete?
rider-app/app/scheduled-rides.tsx       ← listed in activity tab?
rider-app/app/(tabs)/activity.tsx       ← shows scheduled rides?
rider-app/app/chat-driver.tsx           ← polling or WS?
rider-app/store/rideStore.ts            ← all ride actions defined?
backend/routes/rides.py                 ← corporate_account_id in ride creation?
```

### Kick-off prompt
```
You are auditing the Spinr rider app for feature completeness (Dimension 01).

Context:
- Framework: audit-framework/dimensions/01-feature-completeness.md
- Ground rules: audit-framework/ground-rules.md (4-digit OTP is approved, do not flag)
- Output format: audit-framework/templates/audit-output.txt
- Scope: rider-app/ + shared/ + backend/routes/ (rider side only)
- The rider app has 36 screens built with Expo Router (file-based routing)

Your task: Work through every checklist item in dimension 01. For each item:
1. Read the relevant file(s)
2. Write a finding (CRITICAL / HIGH / MEDIUM / LOW / PASS / RECOMMENDATION)
3. Include the file path and line number for every non-PASS finding

Specific areas to verify:
- Is rate-ride.tsx wired into ride-completed.tsx?
- Does fare-split.tsx have a reachable entry point from any screen?
- Does activity tab list scheduled rides (not just completed)?
- Does become-driver.tsx complete the full driver onboarding handoff?
- Is there a data export / account deletion screen (PIPEDA requirement)?
- Is there a legal/terms screen (App Store requirement)?
- Does chat-driver.tsx deliver messages in real-time or polling only?
- Is the corporate ride booking flow accessible from any UI?

Write all findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 01.
Tally severities at the end of the task section.
```

---

## DIMENSION 02 — Authentication & Session Management

### What to audit
Rider auth uses the same backend as driver auth. Verify all compensating controls
are in place and that the rider-specific Firebase audience is enforced.

### Rider-specific risks
- Firebase auth path: must validate `com.spinr.user` audience (not driver bundle ID)
- Token refresh concurrent flood: 401 retry queue in `shared/api/client.ts`
- Profile-complete check: first-time riders routed to `/profile-setup` correctly
- Session persistence: cold-start token hydration in `shared/store/authStore.ts`
- Force-logout: token version increment must also clear rider WebSocket session

### Files to read
```
rider-app/app/login.tsx
rider-app/app/otp.tsx
rider-app/app/profile-setup.tsx
shared/store/authStore.ts
shared/api/client.ts
backend/routes/auth.py
```

### Kick-off prompt
```
You are auditing the Spinr rider app for authentication and session security (Dimension 02).

Context:
- Framework: audit-framework/dimensions/02-authentication.md
- Ground rules: audit-framework/ground-rules.md
  IMPORTANT: 4-digit OTP is approved. Compensating controls (rate limit, lockout, expiry,
  hash) must be CONFIRMED as present. If any compensating control is MISSING, flag as HIGH.
- Output format: audit-framework/templates/audit-output.txt
- Scope: rider-app/app/login.tsx, rider-app/app/otp.tsx, rider-app/app/profile-setup.tsx,
         shared/store/authStore.ts, shared/api/client.ts, backend/routes/auth.py

Your task: Work through every checklist item in dimension 02. For each item:
1. Read the actual code — do not assume it's correct because the driver app was audited
2. Write a finding with file path and line number

Rider-specific extras to verify beyond the standard checklist:
- Firebase audience check: does login.tsx / authStore.ts enforce com.spinr.user bundle?
- Concurrent 401: does client.ts queue requests and refresh once (not N refreshes)?
- Profile-complete gate: does _layout.tsx correctly redirect first-time riders?
- Token version: is it checked on every authenticated request?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 02.
```

---

## DIMENSION 03 — Encryption & Secrets

### What to audit
No live keys in source. Tokens stored in SecureStore. Communications over TLS.
Pickup OTP hashed before DB storage (shared finding with driver app — verify fix carried over).

### Rider-specific risks
- `google-services.json` / `GoogleService-Info.plist` checked into repo — contains Firebase project ID
- Stripe publishable key in `app.config.ts` — publishable is OK, but verify no secret key present
- `EXPO_PUBLIC_*` vars are public by design — verify no secret is prefixed EXPO_PUBLIC_
- Pickup OTP hashing: P0-4 from driver audit — confirm backend fix applies to rider ride creation too

### Files to read
```
rider-app/app.config.ts
rider-app/google-services.json
rider-app/GoogleService-Info.plist
shared/config/
backend/routes/rides.py        ← OTP hash on ride creation
shared/store/authStore.ts      ← SecureStore usage
shared/api/client.ts           ← HTTPS enforcement
```

### Kick-off prompt
```
You are auditing the Spinr rider app for encryption and secrets handling (Dimension 03).

Context:
- Framework: audit-framework/dimensions/03-encryption-secrets.md
- Ground rules: audit-framework/ground-rules.md
  (Stripe test keys sk_test_* → LOW severity only. Firebase project IDs in
  google-services.json are public by design for mobile apps — not a secret.)
- Scope: rider-app/app.config.ts, rider-app/google-services.json,
         rider-app/GoogleService-Info.plist, shared/config/, shared/store/authStore.ts,
         shared/api/client.ts, backend/routes/rides.py

Your task: Work through every checklist item in dimension 03. Specific checks:
1. Are any live Stripe secret keys (sk_live_*) or Supabase service-role keys in any file?
2. Is any secret accidentally prefixed EXPO_PUBLIC_ (which makes it public in the bundle)?
3. Are tokens stored in expo-secure-store (not AsyncStorage)?
4. Is the pickup OTP hashed before saving to the database in rides.py?
   (This was a P0 finding in the driver audit — verify the fix exists for rider rides too)
5. Is all API communication enforced over HTTPS?
6. Are Firebase App Check tokens validated on backend endpoints?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 03.
```

---

## DIMENSION 04 — Input Validation

### What to audit
All user-supplied input validated on both client and server. No raw card data.
Address inputs, promo codes, tip amounts, phone numbers — all need server-side guards.

### Rider-specific risks
- Phone number: must enforce `+1` Canadian/US only (ground rule)
- Promo code: client calls `/promo/validate` but does backend enforce max discount cap?
- Tip amount: custom tip in `ride-completed.tsx` — is there a min/max enforced server-side?
- Stops array: `stops[]` in ride creation — does backend cap stop count? validate coordinates?
- Scheduled time: does backend reject past timestamps for scheduled rides?
- Fare split phones: `createFareSplit(rideId, phones[])` — are phones validated format?
- Wallet top-up amount: min $1, max $500? enforced server-side?
- Address name field: free text — XSS/injection risk in saved addresses display

### Files to read
```
rider-app/app/login.tsx                 ← phone validation
rider-app/app/ride-options.tsx          ← promo apply
rider-app/app/ride-completed.tsx        ← tip custom input
rider-app/app/payment-confirm.tsx       ← payment method
rider-app/app/wallet.tsx               ← top-up amount
rider-app/app/fare-split.tsx           ← phone list
rider-app/store/rideStore.ts           ← createRide payload
backend/routes/rides.py               ← server-side validation
backend/routes/promo.py               ← promo validate
backend/routes/payments.py            ← amount validation
```

### Kick-off prompt
```
You are auditing the Spinr rider app for input validation (Dimension 04).

Context:
- Framework: audit-framework/dimensions/04-input-validation.md
- Ground rules: audit-framework/ground-rules.md
  (Phone numbers must enforce +1 Canada/US only — open country codes are a HIGH finding)
- Scope: rider-app/app/ forms + backend/routes/ validation layer

Your task: Work through every checklist item in dimension 04. Specific checks:
1. Phone input in login.tsx — is +1 country code enforced? Is 10-digit format validated?
2. Promo code — is there a server-side max discount cap in backend/routes/promo.py?
3. Custom tip amount in ride-completed.tsx — min 0, max reasonable cap enforced server-side?
4. Stops array — does backend cap maximum number of stops? validate lat/lng ranges?
5. Scheduled time — does backend reject past timestamps?
6. Fare split phone list — format validated? max participants capped?
7. Wallet top-up amount — min/max enforced at backend schema level?
8. Saved address name — free text field — is it sanitised before display (XSS)?
9. Is there any Zod or runtime schema validation on API responses client-side?

Write findings to: reports/audits/2026-04-19-rider-app-v1.txt under TASK 04.
```

---

## Phase A End-of-Phase Checkpoint

Before moving to Phase B, confirm:
- [ ] All findings written to `reports/audits/2026-04-19-rider-app-v1.txt` under TASK 01–04
- [ ] All CRITICAL findings logged in P0 sprint file
- [ ] All HIGH findings logged in P1 sprint file
- [ ] Pre-audit scan results recorded at top of audit file
- [ ] No Phase A findings left without a severity label
