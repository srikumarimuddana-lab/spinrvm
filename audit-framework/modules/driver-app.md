# Module: Driver App

**Tech stack:** React Native + Expo SDK 54 (Android + iOS)
**Bundle ID:** `com.spinr.driver`
**Root folder:** `driver-app/`
**Related backend routes:** `backend/routes/` — auth, drivers, rides, payments, webhooks, notifications, quests, subscription, documents
**Shared libraries:** `shared/`

---

## Applicable Dimensions

| # | Dimension | Priority | Notes |
|---|---|---|---|
| 01 | Feature completeness | Required | Check all screens in `driver-app/app/driver/` |
| 02 | Authentication | Required | OTP + Firebase paths |
| 03 | Encryption & secrets | Required | `EXPO_PUBLIC_*` exposure |
| 04 | Input validation | Required | Profile setup, vehicle info, document upload |
| 05 | Android & iOS UI/UX | Required | Both platforms, all screen sizes |
| 06 | Real-time | Required | GPS tracking + WebSocket dispatch |
| 07 | State machine | Required | Ride lifecycle |
| 08 | Payments | Required | Stripe Connect payouts |
| 09 | Test coverage | Required | Jest + Maestro |
| 10 | Error handling | Required | Offline queue, ErrorBoundary |
| 11 | Security headers | Partial | Backend headers affect this app |
| 12 | Compliance | Required | PIPEDA, PCI-DSS, document expiry |
| 13 | Notifications/AI | Required | All FCM cases |
| 14 | Performance | Required | Often skipped — don't |
| 15 | Accessibility | Required | AODA + App Store review |
| 16 | i18n / French | Required | Official Languages Act |

---

## Key Files (most important to read)

| File | Why It Matters |
|---|---|
| `driver-app/hooks/useDriverDashboard.ts` | GPS + WebSocket core — most complex file in the app |
| `driver-app/store/driverStore.ts` | Client-side state machine |
| `driver-app/app/driver/index.tsx` | Main dashboard screen |
| `driver-app/components/panels/RideOfferPanel.tsx` | Ride offer UX |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | In-ride UI |
| `driver-app/app/driver/payout.tsx` | Stripe Connect payout flow |
| `driver-app/app/_layout.tsx` | Root layout — FCM setup, notification channels |
| `driver-app/app.config.ts` | Expo config — privacy manifest, plugins |
| `backend/routes/auth.py` | OTP + token flow |
| `backend/routes/rides.py` | Ride lifecycle endpoints |
| `backend/routes/drivers.py` | Driver profile + status |
| `backend/services/dispatch_service.py` | Matching algorithm |
| `backend/utils/document_expiry.py` | Compliance — doc expiry + suspension |
| `shared/api/client.ts` | HTTP client + token refresh |
| `shared/store/authStore.ts` | Auth state management |
| `shared/components/SOSButton.tsx` | Emergency SOS |
| `shared/components/OfflineBanner.tsx` | Offline detection |

---

## Known Approved Decisions (do not re-flag)

- OTP is 4 digits — compensating controls documented in v4 audit
- Hard-coded dev OTP "1234" / "123456" — gated to non-production env
- Stripe test keys — intentional for current testing phase
- Supabase placeholder URLs in `.env.example` — intentional

---

## Previous Audits

| Date | Version | Report |
|---|---|---|
| 2026-04-18 | v4 | `reports/audits/2026-04-18-driver-app-production-readiness-v4.txt` |
| 2026-04-18 | v4 (supplement) | `reports/audits/task14-performance-scalability.txt` |

---

## Screens Inventory

| Screen | Status | Notes |
|---|---|---|
| `app/driver/index.tsx` | Complete | Main dashboard + ride flow |
| `app/driver/earnings.tsx` | Complete | |
| `app/driver/payout.tsx` | Complete | T4A download not wired to UI |
| `app/driver/payout-history.tsx` | Exists | Not fully audited |
| `app/driver/subscription.tsx` | Complete | |
| `app/driver/quests.tsx` | Complete | |
| `app/driver/rides.tsx` | Complete | FlatList perf issues (see Task 14) |
| `app/driver/chat.tsx` | Complete | |
| `app/driver/notifications.tsx` | Partial | Deeplink routing missing |
| `app/driver/profile.tsx` | Complete | |
| `app/driver/settings.tsx` | Partial | Notification prefs not synced to API |
| `app/driver/emergency-contacts.tsx` | Complete | |
| `app/driver/referral.tsx` | Complete | |
| `app/driver/ride-detail.tsx` | Complete | |
| `app/driver/tax-documents.tsx` | Exists | Download not wired to UI |
| `app/driver/addresses.tsx` | Complete | |
| `app/become-driver.tsx` | Complete | Onboarding |
| **payout-history-detail** | Missing | P4 item |
| **report-safety.tsx** | Missing | P4 item |
| **legal.tsx** | Missing | P4 item — required for App Store |
