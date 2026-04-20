# Spinr Mobile Smoke Test Checklist

Manual smoke tests to run before every iOS/Android release. Each flow maps to
an automated Maestro flow where available (noted in brackets).

> **Automation status**: `.maestro/` flows cover the ✅ items. 🔲 items require
> manual verification on device until a Maestro flow is added.

---

## Prerequisites

- iPhone (iOS ≥ 16) and Android (API 34+) physical devices, or
  simulator / emulator is acceptable for non-push flows
- Backend pointed at staging (`EXPO_PUBLIC_API_URL=https://api-staging.spinr.ca`)
- Two test accounts:
  - Rider: `+1 (306) 555-0199` (dev OTP: `1234`)
  - Driver: `+1 (306) 555-0100` (dev OTP: `1234`)
- Driver profile complete, docs verified, Stripe onboarded

---

## 1. Authentication

| # | Step | Expected | Maestro |
|---|------|----------|---------|
| A1 | Open fresh install. Enter rider phone number. Tap **Send Code**. | OTP input appears. | `.maestro/rider/01_login.yaml` |
| A2 | Enter `1234` dev OTP. Tap **Verify**. | Home screen / "Where to?" visible. | ✅ |
| A3 | Kill and relaunch app. | Auto-login, no OTP prompt. | 🔲 |
| A4 | Driver app: login with driver phone. | Dashboard with OFFLINE/ONLINE toggle. | `.maestro/driver/01_login.yaml` |
| A5 | Log out. Re-login. | Session cleared. OTP required. | 🔲 |

---

## 2. Ride Request (Rider)

| # | Step | Expected | Maestro |
|---|------|----------|---------|
| B1 | Tap "Where to?". Type destination. Select from suggestions. | Pickup confirmed, fare estimates shown. | `.maestro/rider/02_request_and_cancel_ride.yaml` |
| B2 | Select vehicle type. Tap **Request Ride**. | "Searching for driver" spinner. | ✅ |
| B3 | Cancel while searching. | Ride cancelled, back to home. | ✅ |
| B4 | Lock phone during search. Unlock. | Searching state still shown. | 🔲 |
| B5 | Schedule a ride 30+ min in future. Receive local push reminder 15 min before. | Reminder notification fires. | `.maestro/rider/03_schedule_and_cancel_ride.yaml` |

---

## 3. Mid-Trip (Rider + Driver)

| # | Step | Expected | Maestro |
|---|------|----------|---------|
| C1 | Driver app: go **ONLINE**. | Status shows ONLINE. | `.maestro/driver/02_go_online.yaml` |
| C2 | Rider requests ride. Driver app shows ride offer. | Offer banner with fare + address. | `.maestro/driver/03_accept_ride.yaml` |
| C3 | Driver accepts. Rider app shows driver ETA + map pin. | Driver location updates every 5 s. | 🔲 |
| C4 | Driver arrives. Rider app: enter OTP on driver screen. | Driver app shows "OTP Confirmed". | `.maestro/driver/04_verify_otp.yaml` |
| C5 | Trip starts. Rider: **Add stop** mid-trip. | Driver app shows updated stop. | 🔲 |
| C6 | Rider sends chat message. Driver receives in-app notification + sound. | Message visible on both screens. | `.maestro/rider/04_mid_trip_chat.yaml` |
| C7 | Driver sends chat reply. Rider receives. | Real-time delivery < 2 s. | `.maestro/driver/07_in_trip_chat.yaml` |
| C8 | Kill and relaunch rider app mid-trip. | Mid-trip UI restores. Driver still shown. | 🔲 |
| C9 | Driver completes trip. Rider sees rating prompt. | Earnings summary on driver screen. | `.maestro/driver/05_complete_trip.yaml` |

---

## 4. SOS / Safety

| # | Step | Expected | Maestro |
|---|------|----------|---------|
| D1 | Rider: tap **SOS** button during trip. | Alert confirms. Admin dashboard shows emergency. | `.maestro/rider/05_sos_button.yaml` |
| D2 | Emergency contacts (if added) receive SMS. | Log visible in backend logs. | 🔲 (requires real SMS) |
| D3 | Driver: same SOS flow from driver app. | Same admin alert, role="driver". | 🔲 |

---

## 5. Push Notifications (FCM / APNs)

| # | Step | Expected | Maestro |
|---|------|----------|---------|
| E1 | Fresh install. Open app. | Push permission prompt appears (iOS). | 🔲 (permission prompt is OS-level) |
| E2 | Grant permission. Login. | FCM token registered via `POST /notifications/register-token`. | 🔲 |
| E3 | Driver receives ride-offer push when app is backgrounded. | Notification appears in tray. Tap opens driver dashboard with offer. | 🔲 |
| E4 | Rider receives "Driver accepted" push when app is closed. | Notification tap deep-links to ride tracker. | 🔲 |
| E5 | Scheduled ride reminder at T-15 min. | Local notification fires even when WS is disconnected. | `.maestro/rider/03_schedule_and_cancel_ride.yaml` (partial) |
| E6 | Revoke push permission. | App degrades gracefully — no crash. WS still works. | 🔲 |

---

## 6. Background Location (Driver)

| # | Step | Expected | Maestro |
|---|------|----------|---------|
| F1 | Driver goes ONLINE. Lock phone. | Location still updates to server every 10 s. | 🔲 (requires device) |
| F2 | Driver app: grant "Always" location permission. | iOS: no prompt on subsequent launches. | 🔲 |
| F3 | Driver denies background location; foreground only. | Trip still works; only updates when app is visible. Warning shown. | 🔲 |
| F4 | iOS: revoke location permission entirely. | Driver auto-taken offline. Toast shown. | 🔲 |

---

## 7. Payments

| # | Step | Expected | Maestro |
|---|------|----------|---------|
| G1 | Pay with wallet (sufficient balance). | Fare deducted. Ride marked paid. | 🔲 |
| G2 | Apply promo code before request. | Discount shown in fare estimate. | 🔲 |
| G3 | Wallet balance insufficient. | Error message. Fallback to card. | 🔲 |
| G4 | Driver: request payout (≥ $10). | Payout row created in DB. Stripe transfer if key set. | `.maestro/driver/06_payout.yaml` |

---

## 8. Driver Offline Guard

| # | Step | Expected | Maestro |
|---|------|----------|---------|
| H1 | Driver has active trip. Attempt to go **OFFLINE**. | 409 error. Toggle reverts. | 🔲 |
| H2 | Driver completes trip. Go OFFLINE. | Succeeds. | 🔲 |

---

## Sign-off

Before shipping a release build, each section must have at least one human
verifier on both iOS and Android. Mark below:

```
Release: __________   Date: __________

iOS (device model: __________)
  Auth:         ☐  Ride:    ☐  Mid-trip: ☐
  SOS:          ☐  Push:    ☐  Payments: ☐

Android (device model: __________)
  Auth:         ☐  Ride:    ☐  Mid-trip: ☐
  SOS:          ☐  Push:    ☐  Payments: ☐

Verified by: __________   __________
```
