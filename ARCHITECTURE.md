# Spinr — Architecture Document

## Overview
Spinr is a ride-sharing platform built for the Canadian market (Saskatchewan-first) with a **0% commission model** — drivers keep 100% of fares and pay a flat subscription fee (Spinr Pass).

---

## System Architecture

```
                         ┌─────────────┐
                         │   CLIENTS   │
                         └──────┬──────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
   ┌──────▼──────┐      ┌──────▼──────┐      ┌──────▼──────┐
   │  Rider App  │      │ Driver App  │      │Admin Dashboard│
   │  Expo/RN    │      │  Expo/RN    │      │  Next.js 16  │
   │  SDK 54     │      │  SDK 54     │      │  Tailwind    │
   │  iOS+Android│      │  iOS+Android│      │  Web         │
   └──────┬──────┘      └──────┬──────┘      └──────┬──────┘
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                │ HTTPS / WSS
                                │
                    ┌───────────▼───────────┐
                    │    FastAPI Backend    │
                    │    Python 3.12       │
                    │    Uvicorn (ASGI)    │
                    │    Docker Container  │
                    └───────────┬───────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
   ┌──────▼──────┐      ┌──────▼──────┐      ┌──────▼──────┐
   │  Supabase   │      │   Stripe    │      │  Firebase   │
   │  PostgreSQL │      │  Payments   │      │  FCM (Push) │
   │  Database   │      │  Connect    │      │  Crashlytics│
   │  Auth       │      │  Cards      │      │  App Check  │
   └─────────────┘      └─────────────┘      └─────────────┘
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Rider App** | React Native (Expo SDK 54) | iOS + Android rider experience |
| **Driver App** | React Native (Expo SDK 54) | iOS + Android driver dashboard |
| **Admin Dashboard** | Next.js 16 + Tailwind + shadcn/ui | Web-based admin panel |
| **Shared Code** | TypeScript modules | API client, components, stores, config |
| **Backend** | FastAPI (Python 3.12) | REST API + WebSocket |
| **Database** | Supabase (PostgreSQL) | All data storage |
| **Payments** | Stripe (Cards + Connect) | Rider payments, driver payouts |
| **Push Notifications** | Firebase Cloud Messaging (FCM) | Push to iOS + Android |
| **Crash Reporting** | Firebase Crashlytics | Production crash monitoring |
| **API Security** | Firebase App Check | Block fake API requests |
| **Maps** | Google Maps Platform | Maps, Places, Directions, Geocoding |
| **SMS (Production)** | Twilio | OTP verification |
| **Hosting** | Railway (Docker) | Backend hosting (test + prod) |
| **Mobile CI/CD** | EAS Build (Expo) | Build + OTA updates |
| **Web CI/CD** | GitHub Actions | Test + deploy |

---

## Directory Structure

```
spinr/
├── backend/                    FastAPI Backend
│   ├── server.py               App entry point
│   ├── dependencies.py         Auth middleware (JWT)
│   ├── db.py                   Database abstraction
│   ├── db_supabase.py          Supabase driver
│   ├── schemas.py              Pydantic models
│   ├── core/config.py          Environment settings
│   ├── routes/
│   │   ├── auth.py             Phone + OTP authentication
│   │   ├── rides.py            Ride lifecycle (30+ endpoints)
│   │   ├── drivers.py          Driver management (40+ endpoints)
│   │   ├── payments.py         Stripe card CRUD + payment processing
│   │   ├── promotions.py       Promo codes (10+ targeting rules)
│   │   ├── admin.py            Admin CRUD + staff + subscriptions
│   │   ├── corporate_accounts.py
│   │   ├── notifications.py    FCM tokens + in-app notifications
│   │   ├── fares.py            Vehicle types + fare engine
│   │   ├── disputes.py         Dispute management
│   │   ├── users.py            User profiles
│   │   ├── addresses.py        Saved places
│   │   ├── webhooks.py         Stripe webhooks
│   │   └── websocket.py        Real-time driver tracking
│   └── utils/
│       └── email_receipt.py    HTML receipt generator
│
├── rider-app/                  Rider Mobile App
│   ├── app/                    Expo Router screens
│   │   ├── (tabs)/             Tab navigation (Home, Activity, Account)
│   │   ├── search-destination  Address search
│   │   ├── ride-options        Vehicle selection
│   │   ├── payment-confirm     Payment + fare breakdown
│   │   ├── driver-arriving     Driver tracking (live map)
│   │   ├── driver-arrived      OTP + driver details
│   │   ├── ride-in-progress    Live ride tracking
│   │   ├── ride-completed      Rating + tip + payment + invoice
│   │   ├── manage-cards        Stripe card management
│   │   ├── saved-places        Favourite locations
│   │   ├── promotions          Promo code entry
│   │   └── privacy-settings    Privacy controls
│   └── store/rideStore.ts      Zustand state management
│
├── driver-app/                 Driver Mobile App
│   ├── app/driver/
│   │   ├── index.tsx           Dashboard + map + online toggle
│   │   ├── earnings.tsx        Earnings breakdown
│   │   ├── payout.tsx          Stripe Connect + payouts
│   │   ├── subscription.tsx    Spinr Pass plans
│   │   └── rides.tsx           Trip history
│   ├── components/
│   │   ├── CarMarker.tsx       3D car marker for maps
│   │   └── dashboard/          Dashboard sub-components
│   └── hooks/
│       └── useDriverDashboard  Location + WebSocket + ride state
│
├── admin-dashboard/            Admin Web Panel
│   └── src/app/dashboard/
│       ├── page.tsx            Stats + revenue dashboard
│       ├── rides/              Ride detail (split-panel)
│       ├── drivers/            Driver verify (split-panel)
│       ├── service-areas/      CONFIG HUB (5 tabs)
│       ├── subscriptions/      Spinr Pass plan management
│       ├── staff/              Multi-admin with module access
│       ├── audit-logs/         Admin action tracking
│       └── ...                 12 more pages
│
├── shared/                     Shared Code
│   ├── api/client.ts           HTTP client with JWT
│   ├── components/             SOSButton, CarMarker, ErrorBoundary
│   ├── config/                 Firebase, Spinr config
│   ├── services/firebase.ts    FCM, Crashlytics, App Check
│   └── store/                  Auth + Location stores (Zustand)
│
└── .github/workflows/          CI/CD Pipelines
    ├── ci.yml                  Production pipeline
    └── test-env.yml            Test environment pipeline
```

---

## CI/CD Pipeline

```
┌──────────────────────────────────────────────────────────┐
│                    CI/CD PIPELINE                        │
└──────────────────────────────────────────────────────────┘

DEVELOPMENT WORKFLOW:

  Developer
      │
      ▼
  Feature Branch ──push──► GitHub
      │
      ▼
  Pull Request to develop
      │
      ├── GitHub Actions (test-env.yml)
      │   ├── Backend: Python tests
      │   ├── Rider App: TypeScript check
      │   ├── Driver App: TypeScript check
      │   └── Admin: Next.js build
      │
      ▼
  Merge to develop ──auto──► Railway (Test Backend)
      │                       https://spinr-backend-test.up.railway.app
      │
      ├── EAS Build (test profile)
      │   ├── Android APK (internal distribution)
      │   └── iOS IPA (internal distribution)
      │
      ▼
  QA Testing on Test Environment
      │
      ▼
  Merge to main ──auto──► Railway (Production Backend)
      │                    https://spinr-backend-production.up.railway.app
      │
      ├── EAS Build (production profile)
      │   ├── Android APK ──► Google Play Store
      │   └── iOS IPA ──► Apple App Store
      │
      ├── Admin Dashboard ──► Vercel / Railway
      │
      └── OTA Updates (JS-only changes, no rebuild)
          eas update --branch production


ENVIRONMENTS:

  ┌────────────┬────────────────────────────────────────────────┐
  │ Environment│ Details                                        │
  ├────────────┼────────────────────────────────────────────────┤
  │ Local Dev  │ Backend: localhost:8000                        │
  │            │ Apps: Expo Go / Dev Client                     │
  │            │ OTP: 1234 (no Twilio)                         │
  │            │ Payments: Stripe test mode                     │
  ├────────────┼────────────────────────────────────────────────┤
  │ Test       │ Backend: spinr-backend-test.up.railway.app    │
  │            │ Apps: EAS build (test profile)                │
  │            │ Branch: develop                                │
  │            │ Auto-deploy on push                            │
  ├────────────┼────────────────────────────────────────────────┤
  │ Production │ Backend: spinr-backend-production.up.railway.app│
  │            │ Apps: EAS build (production profile)           │
  │            │ Branch: main                                   │
  │            │ OTP: Twilio (real SMS)                         │
  │            │ Payments: Stripe live mode                     │
  └────────────┴────────────────────────────────────────────────┘


BUILD PROFILES (eas.json):

  ┌─────────────┬──────────────┬───────────────┬──────────────┐
  │ Profile     │ Dev Server   │ Distribution  │ Backend URL  │
  ├─────────────┼──────────────┼───────────────┼──────────────┤
  │ development │ Required     │ Internal      │ localhost    │
  │ test        │ Required     │ Internal      │ test railway │
  │ preview     │ Not needed   │ APK (standalone)│ prod railway│
  │ production  │ Not needed   │ Store ready   │ prod railway │
  └─────────────┴──────────────┴───────────────┴──────────────┘
```

---

## API Endpoints (100+)

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/send-otp` | Send OTP to phone number |
| POST | `/auth/verify-otp` | Verify OTP and get JWT token |
| GET | `/auth/me` | Get current user profile |

### Rides (30+ endpoints)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/rides/estimate` | Get fare estimates |
| POST | `/rides` | Create ride + auto-match driver |
| GET | `/rides/active` | Get rider's active ride (resume) |
| GET | `/rides/history` | Past rides (completed/cancelled) |
| GET | `/rides/{id}` | Ride details |
| POST | `/rides/{id}/tip` | Add tip |
| POST | `/rides/{id}/rate` | Rate driver |
| POST | `/rides/{id}/cancel` | Cancel ride |
| POST | `/rides/{id}/emergency` | SOS alert |
| POST | `/rides/{id}/process-payment` | Charge card (idempotent) |
| GET | `/rides/{id}/share` | Create share link |

### Drivers (40+ endpoints)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/drivers/me` | Driver profile |
| PUT | `/drivers/{id}/status` | Go online/offline (subscription gated) |
| GET | `/drivers/nearby` | Nearby drivers (service area filtered) |
| GET | `/drivers/balance` | Earnings balance |
| GET | `/drivers/earnings` | Earnings by period |
| POST | `/drivers/rides/{id}/accept` | Accept ride |
| POST | `/drivers/rides/{id}/complete` | Complete ride |
| GET | `/drivers/subscription/plans` | Available Spinr Pass plans |
| POST | `/drivers/subscription/subscribe` | Subscribe to plan |
| POST | `/drivers/stripe-onboard` | Stripe Connect setup |

### Payments
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/payments/cards` | List cards (from Stripe) |
| POST | `/payments/cards` | Add card (Stripe SetupIntent) |
| POST | `/payments/cards/{id}/default` | Set default card |
| DELETE | `/payments/cards/{id}` | Remove card |

### Promotions
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/promo/validate` | Validate code (10+ rules) |
| POST | `/promo/apply` | Apply promo to ride |
| GET | `/promo/available` | User's available promos |

### Admin (50+ endpoints)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/admin/auth/login` | Admin/staff login |
| GET | `/admin/service-areas` | List service areas |
| POST | `/admin/service-areas` | Create (full config hub) |
| GET | `/admin/subscription-plans` | Spinr Pass plans |
| GET | `/admin/staff` | List staff members |
| POST | `/admin/staff` | Create staff with module access |
| GET | `/admin/audit-logs` | Admin action history |
| GET | `/admin/stats` | Dashboard statistics |

---

## Business Model

```
┌──────────────────────────────────────────┐
│           SPINR BUSINESS MODEL           │
│         0% Commission Platform           │
└──────────────────────────────────────────┘

Revenue Sources:
  1. Spinr Pass (Driver Subscriptions)
     ├── Basic:     $19.99/month (4 rides/day)
     ├── Pro:       $49.99/month (unlimited)
     └── Configured per service area

  2. Cancellation Fees
     ├── Driver arrived: $4.50
     │   ├── $4.00 → Driver
     │   └── $0.50 → Platform
     └── Ride started: Full fare

  3. Platform Fees (per ride)
     └── Configurable per service area

Driver Earnings:
  ├── 100% of ride fare (0% commission)
  ├── 100% of tips
  └── Cancellation fee share

Service Area = Primary Configuration Unit:
  ├── Vehicle pricing (base/km/min per type)
  ├── Fees & taxes (platform/city/airport/GST/PST)
  ├── Cancellation fees (with driver/admin split)
  ├── Spinr Pass plans (which plans available)
  └── Required driver documents
```

---

## Security

| Layer | Implementation |
|-------|---------------|
| **API Auth** | JWT tokens (Bearer) |
| **Admin Auth** | JWT + role-based module access |
| **OTP** | Twilio SMS (production), 1234 (dev) |
| **Payments** | Stripe (PCI-DSS compliant, no card data stored) |
| **App Integrity** | Firebase App Check |
| **Crash Monitoring** | Firebase Crashlytics |
| **Push** | Firebase Cloud Messaging (APNs + FCM) |
| **HTTPS** | Enforced on Railway |
| **CORS** | Configured in FastAPI |
| **Rate Limiting** | Per-endpoint rate limits |
| **Audit Trail** | Admin action logging |

---

## Key Features

### Rider App
- Phone + OTP login
- Google Places search + set on map
- Multi-stop rides + scheduling
- Vehicle selection with pricing
- Orange→red gradient routes
- 3D car markers (like Uber/Waze)
- Auto-apply best promo code
- Real-time driver tracking
- SOS emergency button (long-press)
- In-ride chat
- Rating + tipping
- Invoice download/share
- Active ride resume on app launch
- Card management (Stripe)
- Saved places (home/work/favourites)

### Driver App
- Online/offline toggle (subscription gated)
- Live map with GPS tracking
- Ride accept/decline with countdown
- OTP verification at pickup
- Earnings dashboard
- Spinr Pass subscription
- Stripe Connect payouts
- Service area selection
- SOS emergency button
- Referral program

### Admin Dashboard
- Collapsible sidebar with dark mode
- Role-based access (super_admin, operations, support, finance, custom)
- Service area as config hub (5 tabs)
- Split-panel ride/driver detail views
- Spinr Pass plan management
- Promo code engine (10+ targeting rules)
- Staff management with module access
- Audit logs
- CSV export
