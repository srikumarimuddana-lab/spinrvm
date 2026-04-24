# Threat Model — Rider App

**Module:** `rider-app/` (React Native, Expo SDK 54)
**Framework:** STRIDE · **Owner:** `rider-app` + `security` · **Cadence:** 90 days
**Version:** 1.0 · **Date:** 2026-04-24

---

## Trust Boundaries

```
  [Rider (user)] ─── interacts with ───▶ [Mobile device]
                                              │
                                              ├── [Rider app process]
                                              │      │
                                              │      ├─── [AsyncStorage (unencrypted by default)]
                                              │      ├─── [Expo SecureStore (encrypted)]
                                              │      └─── [Firebase FCM token]
                                              │
                                              └── [OS / network / other apps]
                                              │
                                  HTTPS + App Check + JWT
                                              │
                                              ▼
                                  [Backend /rides, /payments, /wallet, /ws/rider]
```

---

## Assets (rider-specific)

| Asset | Class | Storage | Risk if exposed |
|---|---|---|---|
| Rider auth tokens (JWT + refresh) | C4 | Expo SecureStore | Full account takeover |
| FCM push token | C3 | AsyncStorage | Push-notification impersonation |
| Cached home / work address | C3 | Zustand persist (AsyncStorage) | Physical safety |
| Cached payment method last-4 | C3 | AsyncStorage | Re-identification |
| Active-ride driver phone / plate | C3 | In-memory | Driver safety |
| Wallet balance display | C3 | Cached | Targeted fraud if disclosed |

---

## STRIDE Analysis — Rider-Specific

### Spoofing

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| RS-1 | Attacker registers account with victim's phone (SIM-swap) | OTP only; no "remember me" persists cross-device | MEDIUM (industry baseline) |
| RS-2 | Attacker uses cloned rider-app bundle | App Check (Play Integrity + DeviceCheck) | LOW |
| RS-3 | Rogue driver impersonates Spinr support in chat | In-app chat only from verified driver of active ride | MEDIUM — verify ride-scope |
| RS-4 | MITM on public Wi-Fi | TLS + public-key pinning | **OPEN** — pinning not yet confirmed in code |

### Tampering

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| RT-1 | Rider tampers with fare estimate to reduce charge | Server computes fare; rider UI is display-only | LOW |
| RT-2 | Rider fakes GPS coordinates to game pricing / distance | Server-side plausibility check | **HIGH** — not yet implemented (D19) |
| RT-3 | Rider submits > 1 rating per ride | Server idempotency on rating submission | MEDIUM — verify in D19 rider |
| RT-4 | Rider modifies AsyncStorage to impersonate another rider | Tokens in SecureStore, not AsyncStorage | LOW |

### Repudiation

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| RR-1 | Rider disputes "I never took that ride" | Server logs; WS events; ride state audit | LOW |
| RR-2 | Rider disputes charge | Stripe dispute flow + audit log | LOW |
| RR-3 | Rider claims SOS failed | SOS event log + fallback tel:911 (P0-1) | LOW |

### Information Disclosure

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| RI-1 | Driver phone/last-name persists on rider device after ride | Clear ride-scope cache on completion | MEDIUM — verify |
| RI-2 | Home/work address leaked to driver after ride | Driver-facing response schema scrubs after completion | **CRITICAL** — verify during D21 rider audit |
| RI-3 | App logs PII (phone, address) to console in release build | Crash reporter only; console.log stripped in release | MEDIUM — verify in D23 binary audit |
| RI-4 | Screenshots cache expose balance / addresses on app-switcher | iOS blurscreen on background; Android FLAG_SECURE | MEDIUM — verify |
| RI-5 | Clipboard leaks address / fare when copied | Clear clipboard after 60s or avoid copy-to-clipboard | LOW |
| RI-6 | Other apps read rider app storage (rooted/jailbroken device) | Root/jailbreak detection for wallet actions | MEDIUM |

### Denial of Service

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| RD-1 | Rapid ride-create spam (user confused, taps Book 10×) | Client debounce + backend rate limit | LOW |
| RD-2 | Rider cancels 20 rides to exhaust driver pool | Cancellation penalty + abuse scoring | MEDIUM |
| RD-3 | Offline crash loops on startup (corrupt cached state) | Try/catch around Zustand rehydrate; reset on corruption | MEDIUM |

### Elevation of Privilege

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| RE-1 | Rider escalates to driver role via crafted request | Role re-read from DB (CLAUDE.md) | LOW |
| RE-2 | Rider reads another rider's ride via `/rides/{id}` (IDOR) | `WHERE rider_id = auth.uid()` | MEDIUM — verify |
| RE-3 | Rider accesses corporate wallet they don't belong to | Corporate membership check | MEDIUM — verify |
| RE-4 | Cross-app token: rider token accepted by driver backend | **OPEN — DV-10** | HIGH until DV-10 fixed |

---

## Rider-Specific Attack Trees

### RAT-1: Physical stalking via rider app data

```
Goal: Determine a specific rider's home address
├── 1. Become a driver who gets matched with the target
│   ├── 1.1 Wait for dispatch → [random; low yield]
│   └── 1.2 Collude with backend abuser → [covered in AAT-1]
├── 2. After ride, retain pickup/destination in driver app cache
│   └── [If driver app retains — RI-2 covers this]
└── 3. Identify target
    └── [Rider first name + vehicle route may be enough with social OSINT]
```

Mitigation: RI-2 enforcement (strip address from driver response after drop-off)
is a CRITICAL verification target.

### RAT-2: Steal rider wallet balance

```
Goal: Transfer rider's wallet balance to attacker
├── 1. Compromise rider session
│   ├── 1.1 Device theft + no MFA on sensitive action → [MEDIUM]
│   ├── 1.2 Credential phish (no password; OTP only) → [SIM-swap baseline]
│   └── 1.3 MITM on public Wi-Fi → [RS-4 pinning pending]
└── 2. Drain
    ├── 2.1 Take rides on attacker-owned driver account → [KYC limits]
    ├── 2.2 Transfer balance to friend-account → [only if transfer feature exists]
    └── 2.3 Use wallet to pay attacker corporate account → [possible via corporate flow]
```

### RAT-3: SOS abuse / suppression

```
Goal: Prevent a rider in danger from triggering SOS
├── 1. App crash on SOS button → [handled by P0-1 fallback tel:911]
├── 2. Network failure at moment of SOS → [P0-1: Alert + tel:911]
└── 3. Attacker physically prevents phone access → [out of scope; driver training]
```

---

## Residual Risk Register (rider-app specific)

| Threat | Risk score | Owner | Target sprint |
|---|---:|---|---|
| RI-2 (home address leak to driver) | 128 | backend | P0 verify |
| RS-4 (TLS pinning) | 64 | rider-app | P1 |
| RT-2 (GPS spoofing fare game) | 48 | backend | P2 |
| RI-4 (screenshot cache on switcher) | 32 | rider-app | P2 |
| RI-6 (rooted-device wallet access) | 32 | rider-app | P2 |
| RE-4 (cross-app Firebase audience DV-10) | 64 | backend | P1 |

---

## Review Cadence

- Re-run after rider Phase E audit execution
- Re-run every 90 days
- Re-run when any of the following change: SOS flow, payment flow, driver-facing
  response schema, corporate wallet flow

**Next review due:** 2026-07-23 or after rider Phase E audit — whichever earlier.
