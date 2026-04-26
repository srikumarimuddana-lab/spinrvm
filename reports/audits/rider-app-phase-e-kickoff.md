# Rider App — Phase E Kickoff: Dimensions 17–22 (Operational Readiness)

**Module:** `rider-app/` + shared backend routes used by rider  
**Branch:** `claude/review-pending-audits-Pu1aP`  
**Prerequisite:** Phase A–D findings in `reports/audits/2026-04-19-rider-app-v1.txt`  
**Output file:** Append findings to `reports/audits/2026-04-19-rider-app-v1.txt`
or create `reports/audits/2026-04-23-rider-app-v1-phase-e.txt` if the primary
file is too large for a single context window.

---

## Scope

Phase E covers the six operational-readiness dimensions that were added to the
audit framework after the original rider v1 audit (2026-04-19). These dimensions
are not mobile-specific — they evaluate whether the *system around the app*
(logging, recovery, fraud, finance, threat model, third-party chain) is
production-ready.

Run each dimension as **one agent call**. Commit after each call. Do not batch
multiple dimensions — the output schema will be truncated.

| Call | Dimension | Rider-specific focus | Output sentinel |
|---|---|---|---|
| E-1 | D17 Observability | Crash analytics, SOS telemetry, PII in logs | `===AUDIT-COMPLETE=== dim=17` |
| E-2 | D18 DR / BCP | Offline mode, WS reconnect, wallet not lost on crash | `===AUDIT-COMPLETE=== dim=18` |
| E-3 | D19 Fraud | Promo abuse, rating manipulation, impossible-travel, fare-split | `===AUDIT-COMPLETE=== dim=19` |
| E-4 | D20 Financial reconciliation | Rider wallet, fare-split debits, corporate wallet display | `===AUDIT-COMPLETE=== dim=20` |
| E-5 | D21 Threat model / STRIDE | Impersonation, GPS spoof, SOS abuse, payment bypass | `===AUDIT-COMPLETE=== dim=21` |
| E-6 | D22 Third-party risk | Expo, Google Maps, Firebase, Stripe; corporate SSO future | `===AUDIT-COMPLETE=== dim=22` |

---

## Inputs (read once, in order)

1. `audit-framework/dimensions/17-observability.md`  
2. `audit-framework/dimensions/18-dr-bcp.md`  
3. `audit-framework/dimensions/19-fraud.md`  
4. `audit-framework/dimensions/20-financial-reconciliation.md`  
5. `audit-framework/dimensions/21-threat-model.md`  
6. `audit-framework/dimensions/22-third-party.md`  
7. `audit-framework/regulatory-matrix.md` — regulation tag reference  
8. `audit-framework/modules/rider-app.md` — module scope  
9. `audit-framework/ground-rules.md` — what not to flag  

---

## Ground Rules (rider Phase E specific)

- **Do not re-audit D01–D16** — those findings are in the v1.txt file.
- **Shared backend paths**: if a finding affects the backend route used by both
  rider and driver, note `shared_with: driver` and reference the driver finding
  ID if one exists in `reports/audits/2026-04-23-driver-P*-verification.md`.
- **CRITICAL/HIGH** require `file:line:snippet` evidence — no evidence → downgrade.
- Every finding must tag ≥1 regulation ID.

---

## D17 — Observability

**Goal:** Confirm the rider app and its backend routes emit structured, PII-safe,
actionable telemetry. Confirm SLIs are defined and heartbeats are running.

### Checklist

**Crash reporting**
- [ ] `rider-app/` — Sentry or Firebase Crashlytics integrated; verify `app.config.ts`
  or `app/_layout.tsx` for SDK init call
- [ ] Crash events include `ride_id` and error type but NOT PII (phone, name, address)
- [ ] Uncaught promise rejections in `rideStore.ts` reach the crash reporter (not swallowed)

**Structured logging — backend**
- [ ] `backend/core/middleware.py` — every request logs `request_id`, `user_id` (hashed),
  HTTP method, path, status code, latency in JSON format
- [ ] Log lines for rider-side routes (`/rides`, `/payments`, `/wallet`, `/notifications`)
  do not contain raw phone numbers or card PAN
- [ ] `backend/utils/audit_logger.py` — emits structured rows for: ride requested,
  ride completed, payment charged, wallet topped up, SOS triggered

**SLIs / heartbeats**
- [ ] `backend/core/lifespan.py` — all 7 background loops emit a heartbeat log line
  every cycle (so silence = alert)
- [ ] `/healthz` endpoint includes a Redis and Supabase check
- [ ] SLI targets documented (recommend: p99 ride-request → driver-assigned ≤ 30s;
  payment ≤ 5s; WS reconnect ≤ 3s)

**Key files to read:**
- `rider-app/app/_layout.tsx` — crash SDK init
- `rider-app/store/rideStore.ts` — error handling in async actions
- `backend/core/middleware.py` — request logging
- `backend/utils/audit_logger.py` — audit event emission
- `backend/core/lifespan.py` — heartbeat loops

**Rider-specific risk:** SOS trigger failure must always be logged at ERROR level
(finding from P0-1 remediation). Confirm the `triggerEmergency` path in
`rideStore.ts` emits a log entry even when the network call fails.

---

## D18 — Disaster Recovery / Business Continuity

**Goal:** Confirm the rider app degrades gracefully when backend is unreachable,
and that no in-flight ride or payment state is silently lost.

### Checklist

**Offline mode**
- [ ] `rider-app/` — `shared/components/OfflineBanner.tsx` is shown during network loss
- [ ] In-progress ride state is persisted locally (AsyncStorage or Zustand persist) so
  a crash during a trip does not lose `currentRide` on restart
- [ ] Wallet balance is cached; stale label shown when offline (not zero or undefined)

**WebSocket reconnection**
- [ ] `rider-app/hooks/useRiderSocket.ts` — confirm exponential-backoff reconnect on
  WS close; no infinite tight loop; reconnect cap ≤ 60s
- [ ] On reconnect, rider re-subscribes to their active ride channel (no missed events)

**Payment recovery**
- [ ] If `POST /rides/{id}/payment` returns 500, the app retries with the same
  idempotency key (does not create duplicate charges)
- [ ] Rider sees a non-generic error (not "Something went wrong") if payment fails

**Backend PITR / Redis**
- [ ] (Backend — shared) Supabase PITR is configured to ≥ 7 days
- [ ] Redis replica exists for session/rate-limit state (or failover documented)

**Key files to read:**
- `rider-app/hooks/useRiderSocket.ts` — reconnect logic
- `rider-app/store/rideStore.ts` — persistence of `currentRide`
- `rider-app/store/walletStore.ts` — offline wallet display
- `shared/components/OfflineBanner.tsx` — offline detection

---

## D19 — Fraud Prevention

**Goal:** Confirm the rider app and its backend routes resist promo abuse, rating
manipulation, fare-split abuse, and impossible-travel booking patterns.

### Checklist

**Promo code abuse**
- [ ] `backend/routes/promotions.py` (or equivalent) — promo codes are single-use per
  account and validated server-side (not just client-side)
- [ ] New-account promo: phone verification is required before promo credit is applied
  (prevents bulk fake accounts)
- [ ] Promo redemption is logged in `financial_events` or equivalent audit table

**Rating manipulation**
- [ ] `backend/routes/rides.py` (rating endpoint) — rider can only submit one rating
  per ride (idempotent; second submission is 409 or ignored)
- [ ] Driver cannot see their running average until they have also rated the rider
  (mutual blind rating — verify server-side, not just UI)

**Fare-split abuse**
- [ ] `backend/routes/fare_split.py` — split amount ≤ ride fare (cannot split more
  than was charged); each participant can only confirm once
- [ ] Fare-split invites expire (not open indefinitely)

**Impossible-travel / velocity**
- [ ] `backend/services/dispatch_service.py` — if a rider books a second ride while
  one is already `in_progress`, the request is rejected (one-active-ride limit)
- [ ] Rate limit on `POST /rides` — cannot spam ride requests (DoS / surge manipulation)

**SOS abuse**
- [ ] `backend/routes/rides.py` (emergency endpoint) — SOS events are logged with
  location, `ride_id`, and timestamp; repeated false SOS from same account is
  flaggable (no hard block needed, but log volume alerting recommended)

**Key files to read:**
- `backend/routes/promotions.py` — promo validation
- `backend/routes/rides.py` — rating idempotency, one-active-ride guard
- `backend/routes/fare_split.py` — split amount bounds
- `backend/core/middleware.py` — rate limits on ride creation

---

## D20 — Financial Reconciliation

**Goal:** Confirm rider wallet balances, fare debits, fare-split settlements, and
corporate wallet charges reconcile against backend records and Stripe.

### Checklist

**Rider wallet**
- [ ] `rider-app/store/walletStore.ts` — wallet balance fetched fresh on every app
  foreground event (not only on explicit refresh)
- [ ] `backend/routes/wallet.py` — wallet delta uses `corporate_wallet_apply_delta`
  Postgres function (row-level lock + idempotency) — not a raw `UPDATE balance = balance - X`
- [ ] Wallet top-up via Stripe: on `payment_intent.succeeded` webhook, wallet is
  credited exactly once (idempotency key checked in `stripe_events` table)

**Fare settlement**
- [ ] `backend/routes/rides.py` (complete-ride path) — fare debit is atomic with ride
  status transition (both succeed or both roll back — no partial states)
- [ ] Surge multiplier applied server-side only; rider-app fare estimate is a display
  value only and cannot be used to override backend fare

**Fare-split settlement**
- [ ] Each split participant's debit is ≤ their agreed share
- [ ] If a participant's wallet is short, the debit falls back to their payment
  method (not silently zeroed)

**Corporate wallet**
- [ ] `rider-app/` (corporate ride screens) — corporate wallet balance display
  matches `/corporate/wallet/balance` endpoint (not a stale local value)
- [ ] Cost centre assignment is validated server-side (rider cannot change cost
  centre after ride is charged)

**Reconciliation cron**
- [ ] (Backend — shared) A daily cron reconciles Stripe `PaymentIntent` total vs
  `financial_events` total vs wallet table sum — delta alert if mismatch > $0.01

**Key files to read:**
- `rider-app/store/walletStore.ts` — wallet balance refresh
- `backend/routes/wallet.py` — delta helper
- `backend/routes/rides.py` — fare settlement path
- `backend/routes/fare_split.py` — split settlement
- `backend/routes/webhooks.py` — Stripe webhook, idempotency check

---

## D21 — Threat Model / STRIDE

**Goal:** Apply STRIDE to the rider-app attack surface. Focus on threats that are
specific to riders (as opposed to drivers) and on shared paths.

### Rider-specific STRIDE scenarios

| Threat | Scenario | Location | Severity est. |
|---|---|---|---|
| **Spoofing** | Attacker registers as rider with stolen phone number (SIM-swap) | `backend/routes/auth.py` OTP path | HIGH |
| **Spoofing** | Rider uses cloned app bundle to bypass Firebase App Check | `backend/core/middleware.py` App Check | HIGH |
| **Tampering** | Rider modifies fare estimate in local state to underpay | `backend/routes/rides.py` — fare applied server-side? | MEDIUM |
| **Tampering** | Rider submits false GPS coordinates to get lower fare for long trip | `backend/routes/rides.py` — distance server-computed? | HIGH |
| **Repudiation** | Rider disputes charge but audit log missing payment event | `backend/utils/audit_logger.py` | HIGH |
| **Info Disclosure** | Driver receives rider's phone number or home address via API | `backend/routes/rides.py` driver-facing response | CRITICAL |
| **Info Disclosure** | Wallet balance leaked via error message body | `backend/routes/wallet.py` error responses | MEDIUM |
| **Denial of Service** | Rider spam-books and cancels to exhaust driver supply | `backend/core/middleware.py` rate limit on `/rides` | HIGH |
| **Elevation of Privilege** | Rider JWT accepted on driver-only endpoint | `backend/routes/drivers.py` role check | HIGH |
| **Elevation of Privilege** | Rider accesses another rider's ride history via IDOR | `backend/routes/rides.py` — `WHERE rider_id = current_user` | HIGH |

### Checklist

- [ ] Driver-facing ride response strips rider phone number and home/work address
- [ ] `/rides/{id}` only returns data if `rider_id == authenticated_user.id` (IDOR check)
- [ ] Fare is computed server-side using backend-calculated distance — client estimate
  not trusted for settlement
- [ ] `POST /rides` rate-limited per user (prevent surge manipulation)
- [ ] App Check enforced in production (backend rejects requests without valid attestation)
- [ ] JWT role claim for non-admin tokens re-read from DB (per CLAUDE.md convention)

**Key files to read:**
- `backend/routes/rides.py` — IDOR guards, driver-response field filtering
- `backend/routes/auth.py` — role enforcement
- `backend/core/middleware.py` — App Check, rate limiting

---

## D22 — Third-Party Risk

**Goal:** Confirm Spinr's vendor chain for rider-facing features is documented,
contracted, and secure.

### Checklist

**Vendor inventory**
- [ ] `docs/vendor-inventory.md` exists (or equivalent) — confirm Expo SDK, Google Maps,
  Firebase (Auth + FCM + Crashlytics), Stripe, Twilio, Supabase are listed
- [ ] Each vendor row includes: service description, data categories processed,
  hosting region, DPA status, sub-processor disclosure in privacy policy

**Expo SDK / React Native**
- [ ] `rider-app/package.json` — no dependency with a known CVE (run `yarn audit`)
- [ ] Expo SDK version is within Expo's supported window (not EOL)
- [ ] EAS build pipeline: only triggered by `[build]` in commit message (per CLAUDE.md)

**Google Maps**
- [ ] `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` is restricted to rider bundle ID
  (`com.spinr.user`) in Google Cloud Console — not unrestricted
- [ ] Maps API key is NOT in the APK/IPA binary (loaded from env at build time)

**Firebase**
- [ ] Firebase project has separate App IDs for rider vs driver (audience enforcement — see DV-10)
- [ ] FCM token rotation on logout (token not reused for a new user on shared device)

**Stripe**
- [ ] Stripe publishable key is restricted to rider operations only (not driver Stripe Connect key)
- [ ] No Stripe secret key in `rider-app/` codebase or `.env` files

**Supabase**
- [ ] Supabase project region confirmed as Canadian (or cross-border disclosure in place)
- [ ] RLS policies prevent rider from reading another rider's rows in `rides`, `wallets`,
  `addresses` tables

**Key files to read:**
- `rider-app/package.json` — dependency versions
- `rider-app/app.config.ts` — Google Maps key, Firebase config
- `rider-app/.env.example` — confirm no secret keys
- `backend/migrations/` — RLS policy definitions

---

## Output Contract

Each call (E-1 through E-6) must produce:

1. Human-readable findings using `[N-M]` tag format
2. A `===FINDINGS-YAML===` block using the schema from
   `audit-framework/templates/audit-output.txt`
3. A sentinel: `===AUDIT-COMPLETE=== dim=[N] module=rider-app findings=[N]`

**Append** findings to `reports/audits/2026-04-19-rider-app-v1.txt` if it is
within context limits. Otherwise write to
`reports/audits/2026-04-23-rider-app-v1-phase-e.txt`.

Commit after each call.
