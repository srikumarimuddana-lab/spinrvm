# Threat Model — Driver App

**Module:** `driver-app/` (React Native, Expo SDK 54)
**Framework:** STRIDE · **Owner:** `driver-app` + `security` · **Cadence:** 90 days
**Version:** 1.0 · **Date:** 2026-04-24

---

## Trust Boundaries

```
  [Driver (user)] ─── interacts with ───▶ [Mobile device]
                                              │
                                              ├── [Driver app process]
                                              │      ├── [AsyncStorage]
                                              │      ├── [Expo SecureStore (tokens)]
                                              │      ├── [FCM token]
                                              │      └── [Background GPS service]
                                              │
                                  HTTPS + App Check + JWT
                                              │
                                              ▼
                     [Backend /rides, /drivers, /payments, /ws/driver, /documents]
```

---

## Assets (driver-specific)

| Asset | Class | Storage | Risk if exposed |
|---|---|---|---|
| Driver auth tokens | C4 | SecureStore | Account takeover; payout diversion |
| Licence image / photo | C4 | Supabase Storage (not on device after upload) | ID theft |
| SIN last-4 | C4 | Backend only (pgsodium); never in app | CRA identity fraud |
| Stripe Connect account ID | C3 | In-memory / server | Payout redirection |
| Live GPS coordinates | C3 (live), C5 (stored trace) | In-memory; backend | Stalking of driver, location fraud |
| T4A tax documents | C5 | Backend (pdf_url) | CRA identity fraud |
| Rider phone (during active ride) | C3 | In-memory | Stalking of rider |
| Rider pickup/destination addresses | C3 | In-memory (should be wiped post-ride) | Rider stalking (RAT-1) |
| Ride offer data (30s TTL) | C3 | In-memory | Dispatch manipulation |

---

## STRIDE Analysis — Driver-Specific

### Spoofing

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| DS-1 | Fake driver onboards with forged licence | Document verification (manual + automated OCR + expiry checks) | MEDIUM — DS-1 depends on doc-expiry loop which has DV-2 + DV-1 open |
| DS-2 | Driver account stolen via SIM-swap | OTP + lockout (ground-rule 1) | MEDIUM |
| DS-3 | Driver clones app to receive offers without App Check | App Check enforced | LOW |
| DS-4 | Driver logs into multiple devices simultaneously | Session binding to device ID | **VERIFY** during driver Phase E |
| DS-5 | Cross-audience Firebase token (driver token works on rider path) | **OPEN — DV-10** | HIGH until DV-10 fixed |

### Tampering

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| DT-1 | Driver reports fake trip with GPS mocks | Server-side velocity/plausibility check + rider-side confirmation | **HIGH** — D19 not yet audited |
| DT-2 | Driver manually ends trip without arriving | Pickup-confirmation geofence | MEDIUM — verify |
| DT-3 | Driver tampers with mileage in earnings view | Server computes earnings | LOW |
| DT-4 | Driver modifies `is_available` locally to stay online while suspended | **OPEN — DV-1** (dispatch filter) | HIGH until DV-1 fixed |
| DT-5 | Driver rates rider multiple times | Server idempotency | MEDIUM — verify |
| DT-6 | Document-expiry loop's `$set` wrapper silently fails to suspend | **OPEN — DV-2** | HIGH until DV-2 fixed |

### Repudiation

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| DR-1 | Driver claims they never accepted a ride (after no-show) | Accept event logged with timestamp + device | LOW |
| DR-2 | Driver disputes earnings statement | Immutable earnings ledger + reconciliation (D20) | MEDIUM — reconciliation cron pending |
| DR-3 | Driver claims they didn't trigger SOS | SOS event logged + GPS pin | LOW |

### Information Disclosure

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| DI-1 | Driver retains rider PII (phone, address) after ride | Scope-strip from response post-drop-off | **VERIFY** during driver re-audit |
| DI-2 | Driver screenshots app during ride to capture rider data | FLAG_SECURE on sensitive screens | MEDIUM — verify |
| DI-3 | Driver's live GPS exposed to riders outside active ride scope | WS channel keyed to ride; rider-side view terminated on completion | MEDIUM — verify |
| DI-4 | Driver app logs PII in crash reports | Crashlytics PII scrub | **VERIFY** during D23 |
| DI-5 | Driver Stripe Connect payout details visible on shared device | App lock (biometric) required before earnings screen | MEDIUM — verify |
| DI-6 | Driver earnings accessible to attacker with physical device | SecureStore for tokens; biometric re-auth | MEDIUM |
| DI-7 | Driver's personal home address accidentally stored in "Favorites" visible in UI | UI scope: favorites are pickup points, not driver's home | LOW |

### Denial of Service

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| DD-1 | Driver declines many rides to bait the system | Acceptance-rate scoring; visibility in quests | MEDIUM |
| DD-2 | Driver spams fake SOS to tie up responders | Log for review; no auto-block (RAT-3 spirit) | MEDIUM — see DV-9 alerting |
| DD-3 | Background GPS drained battery → driver complaints | Adaptive polling; surface in app | LOW |
| DD-4 | Notification flood from backend | FCM send-rate limit + client backoff | LOW |

### Elevation of Privilege

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| DE-1 | Driver reads data of another driver via IDOR | `WHERE driver_id = auth.uid()` on all endpoints | MEDIUM — verify |
| DE-2 | Driver accesses admin-only endpoint | Role re-read from DB | LOW |
| DE-3 | Driver modifies Stripe Connect ID to redirect payouts | Server-side ownership check + Stripe DB link | MEDIUM — verify during D08 |
| DE-4 | Cross-app token (DV-10) | **OPEN** | HIGH until fixed |

---

## Driver-Specific Attack Trees

### DAT-1: Payout redirection

```
Goal: Redirect a driver's earnings to attacker's Stripe Connect account
├── 1. Compromise driver account → [DS-2 baseline]
├── 2. Change Stripe Connect ID via /drivers/me endpoint
│   ├── 2.1 Does the endpoint allow re-pointing without KYC? → [VERIFY during D08 audit]
│   └── 2.2 Add-new-Connect-account ceremony required?        → [Stripe Connect onboarding flow]
└── 3. Withdraw payout
    └── [Stripe Connect payout schedule]
```

### DAT-2: Fake-trip earnings fraud (GPS spoofing)

```
Goal: Claim payment for a trip that didn't happen
├── 1. Use Android mock-location or jailbroken iOS → [not easily detectable]
├── 2. Start trip from fake pickup, drive fake route, end fake trip
├── 3. Server accepts because:
│   ├── 3.1 GPS velocity within plausibility bounds → [OPEN — no check]
│   ├── 3.2 No rider-side confirmation of pickup/dropoff → [OPEN]
│   └── 3.3 Surge multiplier doesn't flag anomalous uplift → [OPEN]
└── 4. Earnings credited
```

**Mitigation gap:** Server-side velocity/plausibility checks + rider-side
confirmation of pickup/dropoff are missing. File as D19 P1 during backend audit.

### DAT-3: Onboarding identity fraud

```
Goal: Onboard as driver under stolen identity
├── 1. Obtain target's licence image + phone number (physical theft or leak)
├── 2. Complete OTP verification → [needs phone access — SIM-swap required]
├── 3. Upload docs → [OCR + manual review — depends on quality of review]
├── 4. Pass initial CRC → [real-person CRC required via provincial system]
```

Mitigation: multi-factor identity verification (phone + photo-ID selfie match)
is in D03 scope. Driver audit v4 flagged — verify.

---

## Residual Risk Register (driver-app specific)

| Threat | Risk score | Owner | Target sprint |
|---|---:|---|---|
| DT-4 (DV-1 dispatch suspended filter) | 128 | backend | P0 (blocks device test) |
| DT-6 (DV-2 $set wrapper on expiry) | 128 | backend | P0 (blocks device test) |
| DT-1 (fake-trip GPS) | 64 | backend | P1 |
| DS-5 (DV-10 cross-app Firebase) | 64 | backend | P1 |
| DI-1 (PII retention after ride) | 64 | backend + driver-app | P1 |
| DAT-1 step 2 (Stripe Connect rebind) | 48 | backend | P2 verify |

---

## Review Cadence

- Re-run before v5 driver audit kickoff (expected 2026-07)
- Re-run every 90 days
- Re-run when onboarding, earnings, or Stripe Connect flow changes
- Re-run after P0-5 remediation lands (changes dispatch + doc-expiry paths)

**Next review due:** 2026-07-23 or after DV-1 + DV-2 land — whichever earlier.
