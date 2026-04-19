# Module: Rider App

**Status:** Not yet audited
**Tech stack:** React Native + Expo SDK (expected same as driver app)
**Bundle ID:** `com.spinr.user`
**Root folder:** `rider-app/` (assumed — verify actual path)
**Related backend routes:** `backend/routes/` — auth, rides (rider side), payments, notifications, favorites, addresses, corporate_rider, corporate_wallet

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
| 09 | Test coverage | Required | |
| 10 | Error handling | Required | |
| 11 | Security headers | Partial | Shared backend |
| 12 | Compliance | Required | Rider PII exposure to driver |
| 13 | Notifications/AI | Required | Rider-facing notification cases |
| 14 | Performance | Required | Map rendering, live tracking |
| 15 | Accessibility | Required | |
| 16 | i18n / French | Required | |

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
