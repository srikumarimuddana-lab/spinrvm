# Module: Rider App

**Status:** v1 audit complete (2026-04-19) · 184 findings · Remediation verification pending (2026-04-23)
**Verification prompt:** `reports/audits/2026-04-23-rider-app-remediation-verification-prompt.md`
**Tech stack:** React Native · Expo SDK 54 · React 19 · Expo Router 6 · Zustand 5
**Bundle ID:** `com.spinr.user`
**Root folder:** `rider-app/`
**Branch:** `claude/rider-app-audit-iVxpH`
**Related backend routes:** `backend/routes/` — auth, rides (rider side), payments, notifications, favorites, addresses, corporate_rider, corporate_wallet, promo, fare-split

## Confirmed Structure
- **36 screens** (Expo Router file-based)
- **2 stores:** `rideStore.ts` (600+ lines) + `walletStore.ts` (200 lines) + shared `authStore`
- **WebSocket:** `hooks/useRiderSocket.ts` — `/ws/rider/{userId}`
- **Payments:** Stripe PaymentIntent + in-app wallet
- **Push:** Firebase Cloud Messaging via `@react-native-firebase/messaging`

## Audit Plan Files
- Main plan: `reports/audits/2026-04-19-rider-app-audit-plan-v1.md`
- Phase A kick-off (D01–D04): `reports/audits/rider-app-phase-a-kickoff.md`
- Phase B kick-off (D05–D08): `reports/audits/rider-app-phase-b-kickoff.md`
- Phase C kick-off (D09–D12): `reports/audits/rider-app-phase-c-kickoff.md`
- Phase D kick-off (D13–D16): `reports/audits/rider-app-phase-d-kickoff.md`
- Audit findings: `reports/audits/2026-04-19-rider-app-v1.txt` (to be created during execution)
- P0 sprint: `reports/remediation/rider-P0-critical-fix-now.md`
- P1 sprint: `reports/remediation/rider-P1-before-beta.md`
- P2 sprint: `reports/remediation/rider-P2-before-launch.md`
- P3 sprint: `reports/remediation/rider-P3-hardening.md`
- P4 roadmap: `reports/remediation/rider-P4-future-features.md`

---

## Applicable Dimensions

| # | Dimension | Priority | Notes |
|---|---|---|---|
| 01 | Feature completeness | Required | Ride request, tracking, payment, history |
| 02 | Authentication | Required | Same OTP backend as driver app |
| 03 | Encryption & secrets | Required | No driver-specific keys — verify rider keys |
| 04 | Input validation | Required | Address input, payment form |
| 05 | Android & iOS UI/UX | Required | Rider UX differs significantly |
| 06 | Real-time | Required | Live driver tracking on rider's map |
| 07 | State machine | Required | Ride request → matching → in-progress → complete |
| 08 | Payments | Required | Stripe PaymentIntent (rider-side) |
| 09 | Test coverage | Required | Jest + E2E |
| 10 | Error handling | Required | Offline queue, retry on payment fail |
| 11 | Security headers | Partial | Shared backend |
| 12 | Compliance | Required | Rider PII exposure to driver — highest risk |
| 13 | Notifications/AI | Required | Rider-facing notification cases |
| 14 | Performance | Required | Map rendering, live tracking |
| 15 | Accessibility | Required | AODA + App Store review |
| 16 | i18n / French | Required | Official Languages Act |
| 17 | Observability | Required | Crash analytics, structured error events, SOS telemetry |
| 18 | DR / BCP | Required | Offline mode, graceful reconnect, wallet state not lost |
| 19 | Fraud | Required | Promo abuse, rating manipulation, impossible-travel, fare-split abuse |
| 20 | Financial reconciliation | Required | Rider wallet balance, fare-split debits, corporate wallet display |
| 21 | Threat model / STRIDE | Required | Rider-specific: impersonation, GPS spoof, SOS abuse, payment bypass |
| 22 | Third-party risk | Partial | Expo SDK, Google Maps, Firebase, Stripe; corporate SSO if added |
| 23 | Mobile binary / release artifact | Required | Signed APK/IPA, MobSF, PrivacyInfo.xcprivacy, TLS pinning, App Check, SBOM |

**Total applicable dimensions: 23** (all dimensions apply)

**Phase E kickoff file** (D17–D22): `reports/audits/rider-app-phase-e-kickoff.md` — create before v2 re-audit.

---

## Key Areas to Audit (rider-specific)

### Rider-Facing PII Exposure
The driver app audit confirmed driver PII is stripped from rider-facing responses. For the rider audit:
- Rider phone number must NOT be returned to driver (for safety — no direct contact)
- Rider home/work address must NOT be in ride history shown to driver after trip
- Payment method details must NOT be in any driver-facing API response

### Corporate Ride Flows
Backend has `corporate_rider.py` and `corporate_wallet.py`. Verify:
- Corporate ride booking flow
- Cost centre assignment
- Corporate wallet balance display

### Rating System
- Driver rating submission (1–5 stars) after ride
- Rating manipulation protection: can a rider submit multiple ratings?
- Driver cannot see their rating until after they also rate the rider

---

## Pre-Audit Setup

Before auditing, confirm:
1. Rider app root folder path
2. Backend routes used by rider app (different from driver routes)
3. Any rider-specific environment variables

---

## Audit Checklist When Ready

Run dimensions in this order for maximum efficiency:
1. Start with **02 (Auth)** — same backend, quick win to confirm shared auth works
2. Run **01 (Feature completeness)** — map all screens
3. Run **12 (Compliance)** — rider PII exposed to driver is the highest risk
4. Run **08 (Payments)** — PaymentIntent flow on rider side
5. Run remaining dimensions 03–07, 09–16
