# Spinr — Comprehensive Tech Stack & File Mapping
### Architecture Reference, Integration Guide & Alignment Document

**Document Version:** 1.0  
**Date:** 2026-04-10  
**Scope:** Full platform — backend, rider app, driver app, admin dashboard, shared packages, CI/CD  
**Purpose:** Single authoritative reference for every technology, every file, and how they connect  
**Prepared by:** Claude Code (claude-sonnet-4-6) + vms  

---

## Table of Contents

1. [Platform Architecture Overview](#1-platform-architecture-overview)
2. [Technology Inventory — Canonical Versions](#2-technology-inventory--canonical-versions)
3. [Application Surface Map](#3-application-surface-map)
4. [Backend — Full File Mapping](#4-backend--full-file-mapping)
5. [Rider App — Full File Mapping](#5-rider-app--full-file-mapping)
6. [Driver App — Full File Mapping](#6-driver-app--full-file-mapping)
7. [Admin Dashboard — Full File Mapping](#7-admin-dashboard--full-file-mapping)
8. [Shared Package — Full File Mapping](#8-shared-package--full-file-mapping)
9. [Infrastructure & CI/CD File Mapping](#9-infrastructure--cicd-file-mapping)
10. [Integration Map — External Services](#10-integration-map--external-services)
11. [Data Flow & API Routing](#11-data-flow--api-routing)
12. [State Management Architecture](#12-state-management-architecture)
13. [Authentication & Security Layer](#13-authentication--security-layer)
14. [Real-Time Communication Layer](#14-real-time-communication-layer)
15. [Testing Coverage Map](#15-testing-coverage-map)
16. [Environment Variable Reference](#16-environment-variable-reference)
17. [Dependency Graph](#17-dependency-graph)
18. [Architecture Decision Records (ADRs)](#18-architecture-decision-records-adrs)
19. [Alignment Issues & Recommendations](#19-alignment-issues--recommendations)
20. [Quick Reference Cheat Sheet](#20-quick-reference-cheat-sheet)

---

## 1. Platform Architecture Overview

### System Context Diagram (C4 Level 1)

```
                        ┌─────────────────────────────────────────────────────────────────┐
                        │                        SPINR PLATFORM                            │
                        │                                                                   │
  ┌──────────┐          │  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────┐   │
  │  Rider   │◄────────►│  │  Rider App  │   │ Driver App  │   │  Admin Dashboard    │   │
  │ (Passen- │  HTTPS   │  │ (Expo RN)   │   │ (Expo RN)   │   │   (Next.js 16)      │   │
  │  ger)    │          │  └──────┬──────┘   └──────┬──────┘   └──────────┬──────────┘   │
  └──────────┘          │         │                  │                      │              │
                        │         └────────┬─────────┘                     │              │
  ┌──────────┐          │                  │ REST/WebSocket                 │              │
  │  Driver  │◄────────►│         ┌────────▼──────────────────────────────►│              │
  │ (Service │  HTTPS   │         │         FastAPI Backend                 │              │
  │ Provider)│          │         │         (Python 3.12 + Uvicorn)        │              │
  └──────────┘          │         └────┬───────────┬──────────┬────────────┘              │
                        │              │            │          │                            │
  ┌──────────┐          │         ┌────▼───┐  ┌────▼───┐  ┌──▼──────────┐                │
  │  Fleet   │◄────────►│         │Supabase│  │Firebase│  │  Stripe     │                │
  │  Admin   │  HTTPS   │         │(DB+RT) │  │(Push+  │  │  Connect    │                │
  └──────────┘          │         │        │  │AppCheck│  │  (Payments) │                │
                        │         └────────┘  └────────┘  └─────────────┘                │
                        └─────────────────────────────────────────────────────────────────┘
                                              │            │
                                      ┌───────▼──┐  ┌─────▼──────┐
                                      │  Twilio  │  │  Google    │
                                      │   (OTP)  │  │  Maps API  │
                                      └──────────┘  └────────────┘
```

### Container Diagram (C4 Level 2)

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  SPINR PLATFORM — CONTAINER VIEW                                                          │
│                                                                                            │
│  ┌──────────────────┐    ┌──────────────────┐    ┌───────────────────────────────────┐   │
│  │   RIDER APP      │    │   DRIVER APP     │    │      ADMIN DASHBOARD              │   │
│  │                  │    │                  │    │                                   │   │
│  │ Expo SDK 54      │    │ Expo SDK 54      │    │ Next.js 16 (App Router)           │   │
│  │ React Native     │    │ React Native     │    │ React 19 + TypeScript             │   │
│  │ 0.81.5           │    │ 0.81.5           │    │ Tailwind CSS 4                    │   │
│  │ Expo Router 6    │    │ Expo Router 6    │    │ Shadcn UI (Radix-based)           │   │
│  │ Zustand 5        │    │ Zustand 5        │    │ Leaflet 1.9 (Fleet map)           │   │
│  │ Firebase SDK 24  │    │ Firebase SDK 24  │    │ Recharts 3 (Analytics)            │   │
│  │ Stripe RN 0.50   │    │ Stripe RN 0.50   │    │ Zustand 5 (Auth state)            │   │
│  │ RN Maps 1.20     │    │ RN Maps 1.20     │    │ Vitest 4 (Unit tests)             │   │
│  │ Supabase JS 2.95 │    │ Supabase JS 2.95 │    │ Playwright 1.44 (E2E)             │   │
│  │                  │    │                  │    │ axe-core (WCAG 2.1 AA)            │   │
│  │ Bundle: ca.spinr │    │ Bundle:          │    │                                   │   │
│  │         .rider   │    │ com.spinr.driver │    │ Deployed: Vercel                  │   │
│  └────────┬─────────┘    └────────┬─────────┘    └──────────────────┬────────────────┘   │
│           │                       │                                   │                   │
│           └───────────────────────┼───────────────────────────────────┘                   │
│                                   │ HTTPS + WSS                                           │
│           ┌───────────────────────▼──────────────────────────────────────────────────┐    │
│           │                    FASTAPI BACKEND                                        │    │
│           │                                                                            │    │
│           │  Python 3.12 · FastAPI 0.115 · Uvicorn 0.30 (ASGI)                      │    │
│           │  Pydantic v2 · python-dotenv · SlowAPI (rate limiting)                   │    │
│           │  PyJWT · bcrypt · firebase-admin                                          │    │
│           │  stripe · twilio · loguru · sentry-sdk                                   │    │
│           │  pandas · numpy · Decimal (money) · google-generativeai                  │    │
│           │  WebSockets 12 · httpx · aiohttp                                         │    │
│           │                                                                            │    │
│           │  Deployed: Railway / Render (single Docker container)                     │    │
│           └────┬──────────────┬───────────────┬──────────────────────────────────────┘    │
│                │              │               │                                             │
│    ┌───────────▼───┐  ┌───────▼────┐  ┌──────▼──────────┐                                │
│    │   SUPABASE    │  │  FIREBASE  │  │     STRIPE      │                                 │
│    │               │  │            │  │     CONNECT     │                                 │
│    │ PostgreSQL 15 │  │ FCM (Push) │  │                 │                                 │
│    │ Row Level Sec │  │ App Check  │  │ Payments        │                                 │
│    │ Realtime      │  │Crashlytics │  │ Webhooks        │                                 │
│    │ Storage       │  │            │  │ Connect Accts   │                                 │
│    │ Auth          │  │            │  │                 │                                 │
│    └───────────────┘  └────────────┘  └─────────────────┘                                │
│                                                                                            │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Technology Inventory — Canonical Versions

### Backend

| Technology | Package | Version | Role |
|-----------|---------|---------|------|
| Language | Python | 3.12.x | Runtime |
| Framework | FastAPI | ≥ 0.115.0 | API framework |
| ASGI Server | Uvicorn | ≥ 0.30.0 | HTTP/WebSocket server |
| Config | Pydantic Settings | ≥ 2.10.0 | Type-safe settings from env |
| ORM / DB Client | supabase-py | ≥ 2.10.0 | Supabase PostgREST client |
| Auth — JWT | PyJWT | ≥ 2.8.0 | Token signing/verification |
| Auth — Password | bcrypt | ≥ 4.0.0 | Password hashing |
| Auth — Firebase | firebase-admin | ≥ 6.0.0 | FCM, App Check, Crashlytics |
| Rate Limiting | slowapi | ≥ 0.1.9 | Per-endpoint rate limiting |
| HTTP Client | httpx[http2] | ≥ 0.27.0 | Async external API calls |
| HTTP Client | aiohttp | ≥ 3.9.0 | Async HTTP (supplementary) |
| WebSockets | websockets | ≥ 12.0.0 | Real-time ride events |
| Payments | stripe | ≥ 9.0.0 | Stripe Connect integration |
| SMS / OTP | twilio | ≥ 9.0.0 | Phone OTP delivery |
| File Storage | cloudinary | ≥ 1.40.0 | Driver document photos |
| Cloud Storage | boto3 | ≥ 1.34.0 | AWS S3 document storage |
| Logging | loguru | ≥ 0.7.0 | Structured logging |
| Error Monitor | sentry-sdk | ≥ 1.40.0 | Runtime error capture |
| Data | pandas | ≥ 2.0.0 | Analytics, fare calculation |
| Data | numpy | ≥ 1.24.0 | Numerical computation |
| AI/ML | google-generativeai | ≥ 0.4.0 | Gemini AI integration |
| Maps | google-api-python-client | ≥ 2.0.0 | Google Maps / Directions |
| Money | Decimal (stdlib) | 3.12 stdlib | Exact fare arithmetic |
| Linter | ruff | CI-enforced | Python linting + formatting |
| Tests | pytest | ≥ 8.0.0 | Backend unit tests |
| Tests | pytest-asyncio | ≥ 0.23.0 | Async test support |
| Tests | pytest-cov | ≥ 4.1.0 | Coverage reporting |
| Tests | pytest-mock | ≥ 3.12.0 | Mocking |

### Rider App & Driver App (React Native / Expo)

| Technology | Package | Version | Role |
|-----------|---------|---------|------|
| Platform | Expo SDK | ~54.0.0 | Managed/bare workflow |
| Language | TypeScript | ~5.9.2 | Type-safe JavaScript |
| React | react | 19.1.0 | UI library |
| React Native | react-native | 0.81.5 | Mobile runtime |
| Navigation | expo-router | ~6.0.23 | File-based routing |
| State | zustand | ^5.0.0 | Global state stores |
| Maps | react-native-maps | 1.20.1 | MapView + Polyline |
| Push (Android/iOS) | @react-native-firebase/messaging | ^24.0.0 | FCM background/killed push |
| App Integrity | @react-native-firebase/app-check | ^24.0.0 | Device attestation |
| Crash Reporting | @react-native-firebase/crashlytics | ^24.0.0 | Non-fatal error capture |
| Payments | @stripe/stripe-react-native | 0.50.3 | Stripe card UI |
| Database Client | @supabase/supabase-js | ^2.95.3 | Real-time subscriptions |
| Secure Storage | expo-secure-store | SDK 54 | Token persistence (iOS Keychain / Android Keystore) |
| Location | expo-location | SDK 54 | GPS foreground/background |
| Notifications | expo-notifications | ~0.32.16 | Local notification UI |
| File System | expo-file-system | SDK 54 | CSV export temp file |
| Sharing | expo-sharing | SDK 54 | Share CSV via OS sheet |
| Auth Token | expo-secure-store | SDK 54 | JWT secure persistence |
| Network Info | @react-native-community/netinfo | * | Offline detection |
| Linting | eslint-plugin-jsx-a11y | CI-enforced | WCAG 2.1 AA accessibility |
| Tests | jest | ^29.7.0 | Unit tests |
| Tests | @testing-library/react-native | SDK 54 | Component tests |
| E2E | Maestro | YAML flows | Mobile UI automation |
| Build | EAS (Expo Application Services) | Cloud | iOS + Android builds |

### Admin Dashboard (Next.js)

| Technology | Package | Version | Role |
|-----------|---------|---------|------|
| Framework | Next.js | 16.1.6 | React SSR/App Router |
| Language | TypeScript | ^5 | Type-safe JavaScript |
| React | react / react-dom | 19.2.3 | UI library |
| Styling | Tailwind CSS | ^4 | Utility-first CSS |
| Component Library | Shadcn UI (Radix-based) | Latest | Accessible UI components |
| State | zustand | ^5.0.12 | Auth state |
| Charts | recharts | ^3.8.1 | Analytics visualisation |
| Maps | leaflet + react-leaflet | 1.9.4 / 5.0.0 | Fleet map |
| Maps Extras | leaflet-draw + leaflet.heat | 1.0.4 / 0.2.0 | Geofence draw + heatmap |
| PDF Export | jspdf | ^4.2.1 | Report export |
| Unit Tests | vitest | ^4.1.3 | Component unit tests |
| E2E Tests | @playwright/test | ^1.44.0 | Browser automation |
| Accessibility | @axe-core/playwright | ^4.9.0 | WCAG 2.1 AA on every PR |
| Build Tools | wait-on | ^7.2.0 | E2E server readiness |

### Infrastructure / DevOps

| Technology | Version / Spec | Role |
|-----------|---------------|------|
| Container | Docker (Python 3.12.9-slim, multi-stage) | Backend packaging |
| CI/CD | GitHub Actions | Test, lint, scan, build |
| Secrets Scanning | TruffleHog | Blocks merge on verified secret |
| CVE Scanning | Trivy (exit-code: 1 on CRITICAL/HIGH) | Container vulnerability gate |
| Python Linting | ruff | Code quality enforcement |
| Dependency Updates | Dependabot | Weekly pip + 4x npm + actions |
| Mobile Builds | EAS Build | iOS + Android via Expo cloud |
| Backend Deploy | Railway / Render | Single container deployment |
| Frontend Deploy | Vercel | Admin dashboard hosting |
| Coverage | Codecov | PR coverage reports |
| Error Monitoring | Sentry | Runtime error + performance |
| Redis | Redis (production) | Rate limiting + OTP lockout persistence |
| Database | Supabase (PostgreSQL 15) | Primary data store |
| Migrations | backend/migrate.py CLI | Ordered SQL migration runner |

---

## 3. Application Surface Map

```
spinr/
├── backend/              Python 3.12 · FastAPI · Uvicorn
├── rider-app/            Expo SDK 54 · React Native 0.81.5 · Bundle: ca.spinr.rider
├── driver-app/           Expo SDK 54 · React Native 0.81.5 · Bundle: com.spinr.driver
├── admin-dashboard/      Next.js 16 · React 19 · Tailwind 4
├── shared/               TypeScript monorepo package shared by rider + driver
├── frontend/             Expo SDK 54 · Dual-variant (rider/driver via APP_VARIANT)
├── discovery/            Expo SDK 54 · Driver onboarding mini-app
├── agents/               Python · Claude API · Internal AI automation agents
├── docs/                 Markdown · Audit docs, runbooks, architecture
├── .github/workflows/    GitHub Actions CI/CD (5 workflow files)
├── .maestro/             Maestro YAML mobile UI test flows
└── .claude/              Claude Code project configuration
```

---

## 4. Backend — Full File Mapping

### Entry Points

| File | Role | Key Exports / Behaviour |
|------|------|------------------------|
| `backend/server.py` | FastAPI app factory + entry point | Creates `app`, registers all routers, CORS, middleware, lifespan |
| `backend/db.py` | Database abstraction layer | `Database` class wrapping Supabase + MongoDB-style API |
| `backend/db_supabase.py` | Supabase-specific client | Direct Supabase PostgREST calls |
| `backend/supabase_client.py` | Supabase client singleton | `get_supabase_client()` |
| `backend/schemas.py` | All Pydantic request/response models | `UserCreate`, `RideRequest`, `AuthResponse`, `RefreshTokenRequest`, etc. |
| `backend/dependencies.py` | FastAPI `Depends()` functions | `get_current_user()`, `create_jwt_token()`, `create_refresh_token()`, `hash_token()` |
| `backend/validators.py` | Input validation helpers | Address, lat/lng bounds, phone format |
| `backend/geo_utils.py` | Geospatial utilities | Haversine distance, bounding box, nearby driver query |
| `backend/socket_manager.py` | WebSocket connection manager | Per-user channel send, broadcast, room management |
| `backend/sms_service.py` | Twilio OTP wrapper | `send_otp(phone)`, rate-limit-aware |
| `backend/documents.py` | Driver document management | Upload, verify, status |
| `backend/features.py` | Feature flags | Runtime feature toggle checks |
| `backend/settings_loader.py` | Settings hot-reload | Runtime config reloading |
| `backend/onboarding_status.py` | Driver onboarding state machine | Status transitions: pending → verified → active |

### Core Configuration

| File | Role | Key Settings |
|------|------|-------------|
| `backend/core/config.py` | Pydantic Settings — all environment variables | `JWT_SECRET`, `SUPABASE_URL`, `ALLOWED_ORIGINS`, `STRIPE_*`, `TWILIO_*`, `FIREBASE_*`, `ENV`, `ACCESS_TOKEN_EXPIRE_MINUTES=15`, `REFRESH_TOKEN_EXPIRE_DAYS=30` |
| `backend/core/database.py` | DB connection initialisation | Supabase client setup, connection pool |
| `backend/core/lifespan.py` | FastAPI `@asynccontextmanager` lifespan | Startup: DB connect, Redis connect, Firebase init. Shutdown: clean teardown |
| `backend/core/middleware.py` | CORS + request/response middleware | CORS allowlist enforcement, `X-Request-ID` injection, security headers |
| `backend/core/security.py` | JWT encode/decode | `create_access_token()`, `verify_token()`, `hash_password()`, `verify_password()` |

### API Routes

| File | Prefix | Key Endpoints | Authentication |
|------|--------|--------------|---------------|
| `backend/routes/auth.py` | `/auth` | `POST /send-otp`, `POST /verify-otp`, `POST /refresh` | Public (OTP); JWT required (refresh) |
| `backend/routes/users.py` | `/users` | `GET /me`, `PUT /me`, `POST /profile-photo`, `DELETE /account` | JWT required |
| `backend/routes/drivers.py` | `/drivers` | `POST /register`, `GET /nearby`, `POST /accept-ride`, `PATCH /location-batch`, `GET /earnings` | JWT (driver role) |
| `backend/routes/rides.py` | `/rides` | `POST /request`, `GET /{id}`, `PATCH /{id}/status`, `POST /{id}/cancel`, `GET /history` | JWT required |
| `backend/routes/payments.py` | `/payments` | `POST /intent`, `POST /confirm`, `GET /methods`, `POST /payout` | JWT required |
| `backend/routes/admin.py` | `/admin` | `GET /stats`, `GET /drivers`, `GET /rides`, `POST /promote`, `GET /fleet-location` | JWT (admin role) + CODEOWNERS |
| `backend/routes/fares.py` | `/fares` | `POST /estimate`, `GET /surge-multiplier` | JWT required; Redis cache on surge |
| `backend/routes/notifications.py` | `/notifications` | `POST /send`, `GET /history`, `PUT /read` | JWT required |
| `backend/routes/addresses.py` | `/addresses` | `GET /autocomplete`, `GET /geocode`, `POST /saved` | JWT required |
| `backend/routes/promotions.py` | `/promotions` | `GET /`, `POST /apply`, `GET /admin` | JWT / admin |
| `backend/routes/disputes.py` | `/disputes` | `POST /`, `GET /{id}`, `PATCH /{id}/status` | JWT required |
| `backend/routes/corporate_accounts.py` | `/corporate` | `POST /enroll`, `GET /billing`, `POST /invite` | JWT (corporate role) |
| `backend/routes/settings.py` | `/settings` | `GET /`, `PATCH /` | JWT required |
| `backend/routes/webhooks.py` | `/webhooks` | `POST /stripe` (idempotency-safe) | Stripe-Signature header |
| `backend/routes/websocket.py` | `/ws` | `GET /ws/{user_id}` | JWT query param |
| `backend/routes/main.py` | `/` | `GET /health`, `GET /version` | Public |

### Utility Modules

| File | Role | Key Functions |
|------|------|--------------|
| `backend/utils/audit_logger.py` | Structured security event logging | `log_security_event(event, **kwargs)`, `SecurityEvent` constants |
| `backend/utils/analytics.py` | Ride + revenue analytics | Aggregation queries, time-series fare data |
| `backend/utils/cloudinary.py` | Image upload + transform | `upload_document(file)`, `get_secure_url(public_id)` |
| `backend/utils/email_receipt.py` | Post-trip email receipt | Jinja2 template render + SMTP/SendGrid send |
| `backend/utils/error_handling.py` | Global error handler | Sanitised error responses, Sentry breadcrumb logging |
| `backend/utils/rate_limiter.py` | Custom Redis rate limiter | Sliding window, composite key (user_id + IP), `Retry-After` header |
| `backend/utils/crypto.py` | Cryptographic helpers | `hash_otp(code)` SHA-256, `hash_token(raw)` |

### Tests

| File | Tests | Covers |
|------|-------|--------|
| `backend/tests/test_auth.py` | OTP send, OTP verify, lockout, dev bypass isolation | `routes/auth.py` |
| `backend/tests/test_rides.py` | Race condition guard, geofence check, fare Decimal precision | `routes/rides.py`, `routes/drivers.py` |
| `backend/tests/test_drivers.py` | Driver registration, nearby query, location batch | `routes/drivers.py` |
| `backend/tests/test_admin_stats.py` | Admin stats aggregation | `routes/admin.py` |
| `backend/tests/test_geo_utils.py` | Haversine accuracy, bounding box edge cases | `geo_utils.py` |
| `backend/tests/test_db.py` | DB connection, query builder | `db.py`, `db_supabase.py` |
| `backend/tests/test_documents.py` | Upload, status transitions | `documents.py` |
| `backend/tests/test_features.py` | Feature flag read/write | `features.py` |
| `backend/tests/test_location_batch.py` | Batch location update integrity | `routes/drivers.py` |
| `backend/tests/test_sms.py` | OTP delivery mock | `sms_service.py` |
| `backend/tests/test_sanitize_string.py` | Input sanitisation | `validators.py` |
| `backend/tests/conftest.py` | Shared fixtures — DB, auth, mock Stripe | All tests |

---

## 5. Rider App — Full File Mapping

### App Screens (Expo Router — file = route)

| File | Route | Purpose | Key State/Hooks Used |
|------|-------|---------|---------------------|
| `rider-app/app/_layout.tsx` | Root | Navigation shell, auth guard, FCM token registration | `authStore`, `useEffect` |
| `rider-app/app/index.tsx` | `/` | Home — address search, nearby drivers map, book ride | `locationStore`, `MapView`, `SOSButton` |
| `rider-app/app/login.tsx` | `/login` | Phone number entry | `authStore.sendOtp()` |
| `rider-app/app/otp.tsx` | `/otp` | 6-digit OTP verification | `authStore.verifyOtp()`, `authStore.setTokens()` |
| `rider-app/app/profile-setup.tsx` | `/profile-setup` | First-run name + photo | `authStore`, `upload.ts` |
| `rider-app/app/search-destination.tsx` | `/search-destination` | Address autocomplete | `GET /addresses/autocomplete` |
| `rider-app/app/pick-on-map.tsx` | `/pick-on-map` | Drop pin on map for pickup/dropoff | `react-native-maps` |
| `rider-app/app/ride-options.tsx` | `/ride-options` | Vehicle class selection, fare estimate | `GET /fares/estimate` |
| `rider-app/app/ride-status.tsx` | `/ride-status` | Driver searching state | WebSocket `ride_matched` event |
| `rider-app/app/driver-arriving.tsx` | `/driver-arriving` | Live driver location on map | WebSocket `location_update` |
| `rider-app/app/driver-arrived.tsx` | `/driver-arrived` | Driver at pickup confirmation | WebSocket `driver_arrived` |
| `rider-app/app/ride-in-progress.tsx` | `/ride-in-progress` | En-route to destination | WebSocket `location_update`, `SOSButton` overlay |
| `rider-app/app/ride-completed.tsx` | `/ride-completed` | Trip complete, fare summary | `GET /rides/{id}` |
| `rider-app/app/rate-ride.tsx` | `/rate-ride` | Star rating + feedback | `POST /rides/{id}/rating` |
| `rider-app/app/payment-confirm.tsx` | `/payment-confirm` | Payment method confirm | `Stripe.confirmPayment()` |
| `rider-app/app/manage-cards.tsx` | `/manage-cards` | Saved payment methods | `GET /payments/methods` |
| `rider-app/app/chat-driver.tsx` | `/chat-driver` | In-trip text chat | WebSocket `chat` channel |
| `rider-app/app/promotions.tsx` | `/promotions` | Promo code entry + active promos | `GET/POST /promotions` |
| `rider-app/app/ride-details.tsx` | `/ride-details` | Past ride receipt view | `GET /rides/history` |
| `rider-app/app/saved-places.tsx` | `/saved-places` | Home/Work/Saved address CRUD | `GET/POST /addresses/saved` |
| `rider-app/app/settings.tsx` | `/settings` | Notification prefs, language, theme | `PATCH /settings` |
| `rider-app/app/privacy-settings.tsx` | `/privacy-settings` | Data consent, PIPEDA disclosure | Local + `PATCH /settings` |
| `rider-app/app/emergency-contacts.tsx` | `/emergency-contacts` | SOS contact list CRUD | Local secure store |
| `rider-app/app/report-safety.tsx` | `/report-safety` | Safety incident report | `POST /disputes` |
| `rider-app/app/support.tsx` | `/support` | Help + contact | Static + `POST /disputes` |
| `rider-app/app/legal.tsx` | `/legal` | Terms of service, privacy policy | Static render |
| `rider-app/app/become-driver.tsx` | `/become-driver` | Driver signup redirect | Deep link to driver-app |
| `rider-app/app/(tabs)/` | Tab group | Bottom tab navigation shell | `expo-router` tabs |

### Rider Stores

| File | Store | State Managed |
|------|-------|--------------|
| `shared/store/authStore.ts` | `useAuthStore` | `token`, `refreshToken`, `tokenExpiresAt`, `user`, `sendOtp()`, `verifyOtp()`, `setTokens()`, `refreshTokens()`, `logout()` |
| `shared/store/locationStore.ts` | `useLocationStore` | `coords`, `heading`, `accuracy`, `startTracking()`, `stopTracking()` |

---

## 6. Driver App — Full File Mapping

### App Screens

| File | Route | Purpose | Key State/Hooks Used |
|------|-------|---------|---------------------|
| `driver-app/app/_layout.tsx` | Root | Auth guard, FCM ALL 3 lifecycle handlers, module-scope `setBackgroundMessageHandler` | `authStore`, `router.push('/driver')` on FCM tap |
| `driver-app/app/index.tsx` | `/` | Redirect to `/driver` or `/login` | `authStore.token` check |
| `driver-app/app/login.tsx` | `/login` | Phone entry (testID="phone-input") | `authStore.sendOtp()` |
| `driver-app/app/otp.tsx` | `/otp` | OTP verify (testID="otp-input") | `authStore.verifyOtp()` |
| `driver-app/app/profile-setup.tsx` | `/profile-setup` | Name + photo first-run | `authStore`, `upload.ts` |
| `driver-app/app/become-driver.tsx` | `/become-driver` | Driver type selection | `driverStore` |
| `driver-app/app/vehicle-info.tsx` | `/vehicle-info` | Vehicle make/model/plate | `POST /drivers/register` |
| `driver-app/app/documents.tsx` | `/documents` | License, insurance upload | `documentStore`, `cloudinary.py` |
| `driver-app/app/legal.tsx` | `/legal` | Driver ToS | Static |
| `driver-app/app/report-safety.tsx` | `/report-safety` | Safety incident (driver side) | `POST /disputes` |
| `driver-app/app/driver/index.tsx` | `/driver` | Main dashboard — MapView, ride offers, in-app polyline nav, `distanceToPickup` prop | `useDriverDashboard`, `decodePolyline()`, `fetchRoute()` |

### Driver Components

| File | Component | Purpose |
|------|-----------|---------|
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | `<ActiveRidePanel>` | Ride accept/status panel — geofence-gated "Arrived" button (`distanceToPickup` prop), in-app navigation toggle, "Open in Maps" secondary link |
| `driver-app/components/CarMarker.tsx` | `<CarMarker>` | Animated car icon on MapView |
| `driver-app/components/DriverTopBar.tsx` | `<DriverTopBar>` | Online/offline toggle, earnings summary |
| `driver-app/components/index.ts` | Barrel export | Re-exports all components |

### Driver Hooks

| File | Hook | Purpose |
|------|------|---------|
| `driver-app/hooks/useDriverDashboard.ts` | `useDriverDashboard()` | All dashboard logic — WebSocket ride offers, location batch flush (30s), online/offline toggle, ride state machine, FCM foreground message bridge |

### Driver Stores

| File | Store | State Managed |
|------|-------|--------------|
| `driver-app/store/driverStore.ts` | `useDriverStore` | `isOnline`, `activeRide`, `earnings`, `arriveAtPickup(rideId, lat, lng)` — 150m Haversine guard |
| `driver-app/store/documentStore.ts` | `useDocumentStore` | Upload status, document verification state |
| `driver-app/store/languageStore.ts` | `useLanguageStore` | Active locale (en/fr), i18n switching |

### Driver i18n

| File | Content |
|------|---------|
| `driver-app/i18n/en.json` | English strings |
| `driver-app/i18n/fr.json` | French strings (Saskatchewan bilingual) |
| `driver-app/i18n/index.ts` | i18n initialisation, `t()` export |

---

## 7. Admin Dashboard — Full File Mapping

### App Pages (Next.js App Router)

| File | Route | Purpose |
|------|-------|---------|
| `admin-dashboard/src/app/layout.tsx` | Root | HTML shell, ThemeProvider, NextAuth session |
| `admin-dashboard/src/app/page.tsx` | `/` | Redirect to `/dashboard/drivers` |
| `admin-dashboard/src/app/login/page.tsx` | `/login` | Email + password form → NextAuth credential sign-in |
| `admin-dashboard/src/app/error.tsx` | Error boundary | Caught render errors |
| `admin-dashboard/src/app/not-found.tsx` | 404 | Not found page |
| `admin-dashboard/src/app/dashboard/drivers/` | `/dashboard/drivers` | Driver list, approval/rejection, document review |
| `admin-dashboard/src/app/dashboard/rides/` | `/dashboard/rides` | Live ride feed, cancellation override |
| `admin-dashboard/src/app/dashboard/settings/` | `/dashboard/settings` | Platform config (surge multiplier, fare base) |
| `admin-dashboard/src/app/dashboard/promotions/` | `/dashboard/promotions` | Promo code CRUD, redemption analytics |

### Admin Components

| File | Component | Purpose |
|------|-----------|---------|
| `admin-dashboard/src/components/sidebar.tsx` | `<Sidebar>` | Navigation rail — links to all dashboard sections |
| `admin-dashboard/src/components/driver-map.tsx` | `<DriverMap>` | Leaflet real-time fleet map with driver markers |
| `admin-dashboard/src/components/geofence-map.tsx` | `<GeofenceMap>` | Leaflet-draw geofence zone editor |
| `admin-dashboard/src/components/heat-map.tsx` | `<HeatMap>` | Leaflet.heat demand/cancellation heatmap |
| `admin-dashboard/src/components/theme-provider.tsx` | `<ThemeProvider>` | next-themes dark/light mode |
| `admin-dashboard/src/components/ui/` | Shadcn UI primitives | Button, Card, Dialog, Input, Table, Toast, Badge, Select, etc. (30+ components) |

### Admin Lib / Utilities

| File | Role |
|------|------|
| `admin-dashboard/src/lib/api.ts` | Typed fetch wrapper — all backend API calls from admin |
| `admin-dashboard/src/lib/export-csv.ts` | CSV export builder for driver/ride reports |
| `admin-dashboard/src/lib/utils.ts` | `cn()` class name merging (Tailwind + clsx) |

### Admin Tests

| File | Framework | Tests |
|------|-----------|-------|
| `admin-dashboard/src/__tests__/` | Vitest | 12 unit tests — RideCard, DriverCard, StatsPanel, SearchBar, PromotionForm, DateRangePicker |
| `admin-dashboard/src/lib/__tests__/` | Vitest | Utility function tests |
| `admin-dashboard/e2e/auth.setup.ts` | Playwright | Auth setup — mock login, save storage state |
| `admin-dashboard/e2e/login.spec.ts` | Playwright + axe | 5 tests: form render, button gating, bad creds error, redirect on success, WCAG scan |
| `admin-dashboard/e2e/dashboard.spec.ts` | Playwright + axe | 5 tests: all 4 dashboard routes load, WCAG scan on `/login` + `/dashboard/drivers` |

---

## 8. Shared Package — Full File Mapping

The `shared/` package is imported by both rider-app and driver-app via TypeScript path alias `@shared/*`.

| File | Exports | Used By |
|------|---------|---------|
| **API Layer** | | |
| `shared/api/client.ts` | `apiClient`, `setRefreshCallback()`, 401 auto-refresh interceptor | rider-app, driver-app |
| `shared/api/cachedClient.ts` | `cachedGet()` — LRU cache wrapper around apiClient | Both apps |
| `shared/api/upload.ts` | `uploadFile(uri, endpoint)` — multipart form upload | Profile photo, documents |
| **State Stores** | | |
| `shared/store/authStore.ts` | `useAuthStore` — token, user, `sendOtp()`, `verifyOtp()`, `setTokens()`, `refreshTokens()`, `logout()` | Both apps |
| `shared/store/locationStore.ts` | `useLocationStore` — coords, heading, `startTracking()` | Both apps |
| **Components** | | |
| `shared/components/AppMap.tsx` | `<AppMap>` — MapView wrapper (React Native) | Both apps |
| `shared/components/AppMap.web.tsx` | `<AppMap>` — Leaflet wrapper (web fallback) | frontend/ (web) |
| `shared/components/CarMarker.tsx` | `<CarMarker>` — Animated car SVG marker | Both apps |
| `shared/components/CustomAlert.tsx` | `<CustomAlert>` — Cross-platform modal alert | Both apps |
| `shared/components/ErrorBoundary.tsx` | `<ErrorBoundary>` — React error boundary with Sentry capture | Both apps (root `_layout.tsx`) |
| `shared/components/ErrorScreen.tsx` | `<ErrorScreen>` — Fallback UI on boundary catch | Both apps |
| `shared/components/OfflineBanner.tsx` | `<OfflineBanner>` — NetInfo-driven "No connection" banner | Both apps |
| `shared/components/SOSButton.tsx` | `<SOSButton>` — Permanent floating SOS overlay; dials emergency + POST /disputes | rider-app only |
| **Services** | | |
| `shared/services/firebase.ts` | `initFirebase()`, `setBackgroundMessageHandler()`, `getInitialNotification()`, `onNotificationOpenedApp()` | Both apps |
| **Config** | | |
| `shared/config/firebaseConfig.ts` | Firebase project config object (loaded from env) | `shared/services/firebase.ts` |
| `shared/config/spinr.config.ts` | Platform constants: API base URL, map defaults, fare config | Both apps |
| `shared/config/supabase.ts` | Supabase JS client singleton | Both apps |
| `shared/config/index.ts` | Barrel re-export of all config | Both apps |
| **Utilities** | | |
| `shared/utils/logger.ts` | `logger.debug/info/warn/error()` — loguru-style structured logging with Sentry breadcrumbs | Both apps |
| `shared/cache/index.ts` | Simple LRU in-memory cache (TTL-aware) | `cachedClient.ts` |
| `shared/assets/carImage.ts` | Base64-encoded car icon (no network dependency) | `CarMarker` |
| **Types** | | |
| `shared/types/expo-linear-gradient.d.ts` | Type declaration stub for `expo-linear-gradient` | Both apps |

---

## 9. Infrastructure & CI/CD File Mapping

### GitHub Actions Workflows

| File | Trigger | Jobs |
|------|---------|------|
| `.github/workflows/ci.yml` | Push/PR to `main`, `develop` | 13 jobs: secrets-scan (TruffleHog), container-scan (Trivy), backend-lint (ruff), backend-test (pytest), frontend-test (Jest rider), driver-test (Jest driver), admin-test (Vitest), admin-build (Next.js build), rider-type-check, driver-type-check, e2e-test (Playwright), a11y-lint (jsx-a11y), smoke-test (scaffold, `if: false`) |
| `.github/workflows/deploy-backend.yml` | Push to `main` | Deploy to Railway — `if: false` (disabled in audit fork) |
| `.github/workflows/eas-build.yml` | Manual / tag | EAS Build for iOS + Android — `if: false` (disabled in audit fork) |
| `.github/workflows/apply-supabase-schema.yml` | Manual | Run SQL migrations via Supabase CLI — `if: false` |
| `.github/workflows/test-env.yml` | Manual | Spin up test environment — `if: false` |

### Docker

| File | Stage | Key Actions |
|------|-------|------------|
| `backend/Dockerfile` | `builder` (Python 3.12.9-slim) | Install build dependencies, compile wheels from `requirements.txt` |
| `backend/Dockerfile` | `runtime` (Python 3.12.9-slim) | Add user `spinr` (uid 1001, non-root), copy wheels, expose port 8000 |
| `backend/Dockerfile` | `HEALTHCHECK` | `curl -f http://localhost:8000/health || exit 1` every 30s |
| `backend/Dockerfile` | `CMD` | `uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1` |

### Claude Code Configuration

| File | Role |
|------|------|
| `.claude/settings.json` | Model pin (`claude-sonnet-4-6`), allow/deny permission matrix, attribution footers |
| `.claude/settings.local.json` | Session-scoped tool approvals — **gitignored** |
| `.claude/commands/commit.md` | `/commit` skill — conventional commit with spinr scope mapping and safety gates |
| `.claude/commands/pr.md` | `/pr` skill — PR creation with spinr template |
| `.claude/commands/review.md` | `/review` skill — code review checklist |
| `.claude/commands/start.md` | `/start` skill — sprint start checklist |
| `.claude/commands/status.md` | `/status` skill — project status summary |
| `.claude/hooks/pre-commit` | 5-check security hook (secrets, forbidden files, PII in logs, branch check, money arithmetic) |
| `.claude/launch.json` | 6 dev server configs for `preview_start` |

### EAS Build

| File | Role |
|------|------|
| `rider-app/eas.json` | Build profiles: `development` (Expo Go), `preview` (internal distribution), `production` (App Store / Play Store) |
| `driver-app/eas.json` | Same profile structure as rider-app |
| `rider-app/app.config.ts` | EAS project ID: `8f1e4f60-720e-46b0-9b71-33c13d3af043` |
| `driver-app/app.config.ts` | EAS project ID: `1ed02cf4-97cb-4678-b5a2-0881f89abaa8` |

### Maestro Mobile E2E

| File | Flow | Tests |
|------|------|-------|
| `.maestro/rider/01_login.yaml` | Rider login | Launch app, enter phone, tap Send OTP, assert OTP screen |
| `.maestro/rider/02_request_and_cancel_ride.yaml` | Ride request + cancel | Enter destination, select vehicle, request ride, cancel within grace period |
| `.maestro/driver/01_login.yaml` | Driver login | Launch driver app, enter phone, OTP verify, assert dashboard |
| `.maestro/driver/02_go_online.yaml` | Go online | Toggle online, assert map shows driver pin |

---

## 10. Integration Map — External Services

### Service Dependency Matrix

| Service | Used By | Auth Method | Key Operations | Failure Mode |
|---------|---------|------------|---------------|-------------|
| **Supabase (PostgreSQL)** | Backend (primary DB) | `SUPABASE_SERVICE_ROLE_KEY` | All CRUD, real-time subscriptions, RLS | Backend returns 503 — retry with exponential backoff |
| **Supabase (Realtime)** | Rider + Driver apps (JS client) | `SUPABASE_ANON_KEY` + JWT | Live location updates, ride status | WebSocket fallback |
| **Supabase (Storage)** | Backend | Service role key | Driver document uploads | Cloudinary fallback |
| **Firebase (FCM)** | Backend (push sender) + Both apps (receiver) | `FIREBASE_SERVICE_ACCOUNT_JSON` (backend) / `google-services.json` (apps) | Ride offer push, background/killed state | Silent failure — driver misses offer |
| **Firebase (App Check)** | Both apps → Backend | `@react-native-firebase/app-check` → `firebase-admin` verify | Device attestation on every API call | 403 if App Check fails |
| **Firebase (Crashlytics)** | Both apps | `@react-native-firebase/crashlytics` | Non-fatal error capture, session logging | Non-blocking |
| **Stripe Connect** | Backend + Both apps | `STRIPE_SECRET_KEY` (backend) / `STRIPE_PUBLISHABLE_KEY` (apps) | Payment intents, card confirmation, driver payouts, webhooks | Payment fails gracefully |
| **Twilio** | Backend | `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` | OTP SMS delivery | 503 returned to client |
| **Google Maps Platform** | Backend (Directions), Apps (MapView) | `GOOGLE_MAPS_API_KEY` | Geocoding, route polyline, place autocomplete | Fare estimate unavailable |
| **Cloudinary** | Backend | `CLOUDINARY_URL` | Driver license/insurance photo upload + CDN | Fall back to Supabase Storage |
| **AWS S3** | Backend | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | Document cold storage | Cloudinary primary |
| **Sentry** | Backend + All apps | `SENTRY_DSN` (per surface) | Runtime errors, performance traces | Non-blocking |
| **Redis** | Backend | `REDIS_URL` | OTP lockout state, rate limit counters, fare cache | Falls back to in-memory (not restart-safe) |
| **Google Gemini AI** | Backend + `agents/` | `GOOGLE_API_KEY` | AI-powered demand forecasting, dispute summarisation | Non-blocking |

### Integration Data Flow

```
Rider books ride:
  1. Rider App  → POST /rides/request       → Backend
  2. Backend    → GET /fares/estimate        → Redis cache (or Google Directions API)
  3. Backend    → WebSocket broadcast        → Nearby driver apps (via socket_manager.py)
  4. Backend    → FCM push (data payload)    → Firebase → Driver device (killed/background)
  5. Driver App → POST /drivers/accept-ride → Backend (optimistic lock on status='searching')
  6. Backend    → WebSocket 'ride_matched'   → Rider App
  7. Backend    → POST /payments/intent      → Stripe API
  8. Driver App → Live location PATCH /drivers/location-batch (30s buffer) → Backend → WebSocket → Rider App
  9. Driver App → PATCH /rides/{id}/status = 'completed' → Backend
 10. Backend    → POST /stripe/payout        → Stripe Connect → Driver bank
 11. Backend    → email_receipt.py           → SMTP → Rider email inbox
```

---

## 11. Data Flow & API Routing

### Base URL Configuration

| Environment | Backend URL | Admin Dashboard URL |
|-------------|------------|-------------------|
| Development | `http://localhost:8000` | `http://localhost:3000` |
| Staging | `https://api-staging.spinr.ca` | `https://admin-staging.spinr.ca` |
| Production | `https://api.spinr.ca` | `https://admin.spinr.ca` |

Set via `shared/config/spinr.config.ts` → read from `EXPO_PUBLIC_API_URL` (apps) or `NEXT_PUBLIC_API_URL` (admin).

### Request Flow

```
Mobile App Request:
  shared/api/client.ts
    → Attach Bearer token from authStore
    → Add X-Request-ID header (UUID v4)
    → HTTPS to backend
    → 401? → call refreshTokens() → retry once → logout if fails

Backend Request Handler:
  FastAPI router
    → CORSMiddleware (origin allowlist)
    → X-Request-ID middleware (inject if missing)
    → SlowAPI rate limiter
    → Depends(get_current_user) → PyJWT decode → Supabase user lookup
    → Firebase App Check verify (if APP_CHECK_ENFORCEMENT=true)
    → Route handler
    → log_security_event() on auth/payment/ride events
    → Response
```

### WebSocket Events Reference

| Event Type | Direction | Payload | Trigger |
|-----------|-----------|---------|---------|
| `new_ride_offer` | Backend → Driver | `{ride_id, pickup, dropoff, fare, distance}` | Ride requested, driver nearby |
| `ride_taken` | Backend → Driver | `{ride_id, message}` | Another driver accepted first (race lost) |
| `ride_matched` | Backend → Rider | `{driver_id, eta, driver_info}` | Driver accepted ride |
| `location_update` | Driver → Backend → Rider | `{lat, lng, heading}` | 30s batch from driver app |
| `driver_arrived` | Backend → Rider | `{ride_id}` | Driver tapped "I've Arrived" (geofence validated) |
| `ride_completed` | Backend → Rider | `{fare, receipt_url}` | Driver ended trip |
| `chat` | Bidirectional | `{message, sender_id, ts}` | In-trip chat |
| `connection_ack` | Backend → Client | `{user_id, ts}` | On WS connect |

---

## 12. State Management Architecture

All state uses **Zustand v5** — no Redux, no Context API for global state.

### Store Topology

```
┌─────────────────────────────────────────────────────────────┐
│                    SHARED STORES (both apps)                  │
│                                                               │
│  authStore          locationStore                             │
│  ├─ token           ├─ coords                                 │
│  ├─ refreshToken    ├─ heading                                │
│  ├─ tokenExpiresAt  ├─ accuracy                               │
│  ├─ user            └─ startTracking() / stopTracking()       │
│  ├─ sendOtp()                                                 │
│  ├─ verifyOtp()                                               │
│  ├─ setTokens()   ← called after verify-otp response         │
│  ├─ refreshTokens() ← called by API client 401 handler       │
│  └─ logout()      ← called on refresh failure                │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    DRIVER-ONLY STORES                         │
│                                                               │
│  driverStore                documentStore    languageStore    │
│  ├─ isOnline                ├─ uploadStatus  ├─ locale       │
│  ├─ activeRide              ├─ documents     └─ setLocale()  │
│  ├─ earnings                └─ upload()                      │
│  └─ arriveAtPickup(rideId,                                   │
│       lat, lng)  ← 150m Haversine guard                      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    ADMIN DASHBOARD STORE                      │
│                                                               │
│  authStore (admin-dashboard/src/store/authStore.ts)          │
│  ├─ session (NextAuth)                                        │
│  ├─ isAdmin                                                   │
│  └─ logout()                                                  │
└─────────────────────────────────────────────────────────────┘
```

### Token Persistence

| Platform | Storage | Keys |
|---------|---------|------|
| iOS | Keychain (via expo-secure-store) | `auth_token`, `refresh_token`, `token_expires_at` |
| Android | Android Keystore (via expo-secure-store) | Same keys |
| Admin (web) | NextAuth session cookie (HttpOnly, Secure) | Managed by NextAuth |

---

## 13. Authentication & Security Layer

### Auth Flow Diagram

```
Phone Login (OTP):
  1. POST /auth/send-otp   { phone }
     → Twilio SMS → 6-digit code → SHA-256 hash stored in DB
     → OTP lockout check: 5 failures/hour → 24h lockout (Redis-backed)

  2. POST /auth/verify-otp { phone, code }
     → SHA-256(code) matched against DB hash
     → On success:
         access_token  = JWT signed(JWT_SECRET, exp=15min)
         refresh_token = secrets.token_urlsafe(32) stored as SHA-256 hash
         → both returned in AuthResponse
         → apps: setTokens(access, refresh, expires_in)
         → stored in expo-secure-store
     → On failure:
         record_otp_failure(phone)
         if failures ≥ 5 in 1hr → 24h lockout → 429 + Retry-After header

  3. API calls:
     Authorization: Bearer <access_token>
     → PyJWT decode → exp check → user lookup → Depends(get_current_user)

  4. Token expiry (15min):
     → client.ts 401 handler → POST /auth/refresh { refresh_token }
     → Backend: SHA-256(raw) matched → old token revoked → new pair issued
     → Retry original request with new access token

  5. Refresh failure → authStore.logout() → navigate to /login
```

### Security Controls Active

| Control | Implementation | File |
|---------|--------------|------|
| JWT secret min 32 chars | Startup `RuntimeError` guard | `backend/core/config.py` |
| JWT lifetime 15 minutes | `ACCESS_TOKEN_EXPIRE_MINUTES=15` | `backend/core/config.py` |
| JWT refresh rotation | SHA-256 stored hash, revoke on use | `backend/dependencies.py` |
| OTP 6 digits | `generate_otp(k=6)` | `backend/dependencies.py` |
| OTP hashed at rest | `hash_otp(code)` → SHA-256 | `backend/utils/crypto.py` |
| OTP cumulative lockout | 5 failures/hour → 24h (Redis) | `backend/routes/auth.py` |
| Dev OTP bypass isolated | `if not _is_production` gate | `backend/routes/auth.py` |
| CORS allowlist | Explicit origin list, no wildcard | `backend/core/config.py` |
| CORS startup guard | `RuntimeError` if `*` in production | `backend/core/middleware.py` |
| Race condition guard | Optimistic lock on `status='searching'` | `backend/routes/drivers.py` |
| Geofence arrival | 150m Haversine in `driverStore` | `driver-app/store/driverStore.ts` |
| Docker non-root | `USER spinr` (uid 1001) | `backend/Dockerfile` |
| Container CVE gate | Trivy `exit-code: 1` on CRITICAL/HIGH | `.github/workflows/ci.yml` |
| Secrets in CI | TruffleHog on every PR | `.github/workflows/ci.yml` |
| Pre-commit hooks | 5-check suite | `.claude/hooks/pre-commit` |
| Audit logging | `log_security_event()` structured | `backend/utils/audit_logger.py` |
| WCAG 2.1 AA | jsx-a11y lint + axe-core in CI | `.github/workflows/ci.yml` |
| Stripe idempotency | Idempotency key on webhook handler | `backend/routes/webhooks.py` |
| PIPEDA disclosure | Privacy notice at OTP screen | `rider-app/app/otp.tsx` |
| App Check | `@react-native-firebase/app-check` | `shared/services/firebase.ts` |

---

## 14. Real-Time Communication Layer

### WebSocket Architecture

```
backend/socket_manager.py
  ├─ ConnectionManager class
  │   ├─ active_connections: Dict[str, WebSocket]
  │   │   key: "rider_{user_id}" | "driver_{user_id}"
  │   ├─ connect(websocket, user_id)
  │   ├─ disconnect(user_id)
  │   ├─ send_personal_message(data, user_id)
  │   └─ broadcast_to_drivers(data, driver_ids)
  │
  └─ backend/routes/websocket.py
      GET /ws/{user_id}?token=<jwt>
      → JWT verify on connect
      → Register in ConnectionManager
      → Receive events → route to handlers
```

### Driver App WebSocket (`driver-app/hooks/useWebSocket.ts`)

```
Connection lifecycle:
  connect() → wss://api.spinr.ca/ws/{user_id}?token=<jwt>
  on message: dispatch to useDriverDashboard event handlers
  on close/error:
    attempt++ 
    delay = min(BASE(1s) * 2^attempt, MAX(60s)) + random(0, 0.3 * delay)
    setTimeout(reconnect, delay)
  AppState 'active' → force reconnect (fixes background stale connection)
  Connection status: 'connected' | 'connecting' | 'disconnected'
    → <OfflineBanner> shown when 'disconnected'
```

### FCM Push (Driver App — all 3 states)

```
Module scope (driver-app/app/_layout.tsx):
  setBackgroundMessageHandler(async (msg) => { ... })  ← MUST be here, not in component

Inside useEffect (driver-app/app/_layout.tsx):
  // Killed state:
  const initial = await getInitialNotification()
  if (initial?.data?.type === 'new_ride_offer') router.push('/driver')
  
  // Background state:
  const unsub = onNotificationOpenedApp((msg) => {
    if (msg?.data?.type === 'new_ride_offer') router.push('/driver')
  })
  return () => unsub()  ← cleanup

  // Foreground: handled in useDriverDashboard via onMessage()
```

---

## 15. Testing Coverage Map

### Coverage by Surface

| Surface | Type | Framework | Files | Test Count | Coverage Focus |
|---------|------|-----------|-------|-----------|---------------|
| Backend | Unit | pytest | 12 test files | 12+ tests | Auth, rides, drivers, geo, DB |
| Backend | Lint | ruff | All `.py` | CI-enforced | Code quality |
| Rider App | Unit | Jest + @testing-library | `rider-app/__tests__/` | 12 tests | Auth flow, OTP, location |
| Driver App | Unit | Jest + @testing-library | `driver-app/__tests__/` | 15 tests | Dashboard, earnings, geofence |
| Admin Dashboard | Unit | Vitest | `src/__tests__/` | 12 tests | RideCard, DriverCard, StatsPanel, forms |
| Admin Dashboard | E2E | Playwright | `e2e/*.spec.ts` | 10 tests | Login, auth, all 4 routes, WCAG |
| Admin Dashboard | Accessibility | axe-core | Via Playwright | Per-page | WCAG 2.1 AA (wcag2a, wcag2aa, wcag21aa) |
| Rider App | Mobile E2E | Maestro | `.maestro/rider/` | 2 flows | Login, request + cancel ride |
| Driver App | Mobile E2E | Maestro | `.maestro/driver/` | 2 flows | Login, go online |
| All surfaces | Accessibility lint | jsx-a11y | All TSX | CI-enforced | WCAG labels, roles, hints |
| All surfaces | Type check | TypeScript strict | All `.ts/.tsx` | CI-enforced | Type safety |

### Test Configuration Files

| File | Purpose |
|------|---------|
| `backend/tests/conftest.py` | Shared fixtures: DB, mock auth, mock Stripe, mock Twilio |
| `rider-app/jest.config.js` | ts-jest, moduleNameMapper for `@shared/*`, setup files |
| `driver-app/jest.config.js` | Same as rider, plus `@hooks/*`, `@styles/*` aliases |
| `admin-dashboard/vitest.config.ts` | jsdom environment, `@/*` alias, setup file |
| `admin-dashboard/vitest.setup.ts` | `@testing-library/jest-dom` matchers |
| `admin-dashboard/playwright.config.ts` | Chromium-only, CI retries, baseURL from env, reuse auth state |

---

## 16. Environment Variable Reference

### Backend (`.env` → `backend/core/config.py`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENV` | ✅ | `development` | `production` enables all security guards |
| `JWT_SECRET` | ✅ production | ❌ empty | Min 32 chars — startup fails if absent in production |
| `SUPABASE_URL` | ✅ | — | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | — | Full DB access key (backend only, never client) |
| `SUPABASE_ANON_KEY` | ✅ | — | Public client key (safe in apps) |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | ✅ | — | Base64-encoded service account JSON |
| `STRIPE_SECRET_KEY` | ✅ | — | `sk_live_*` or `sk_test_*` |
| `STRIPE_WEBHOOK_SECRET` | ✅ | — | Webhook signature verification |
| `STRIPE_PUBLISHABLE_KEY` | ✅ | — | Shared with apps via app.config.ts |
| `TWILIO_ACCOUNT_SID` | ✅ | — | SMS OTP delivery |
| `TWILIO_AUTH_TOKEN` | ✅ | — | SMS OTP delivery |
| `TWILIO_FROM_NUMBER` | ✅ | — | Sending phone number |
| `GOOGLE_MAPS_API_KEY` | ✅ | — | Geocoding + Directions API |
| `GOOGLE_API_KEY` | ⚠️ | — | Gemini AI (optional for AI features) |
| `ALLOWED_ORIGINS` | ✅ production | `*` | Comma-separated origin allowlist |
| `CLOUDINARY_URL` | ⚠️ | — | Image upload + CDN |
| `AWS_ACCESS_KEY_ID` | ⚠️ | — | S3 document storage |
| `AWS_SECRET_ACCESS_KEY` | ⚠️ | — | S3 document storage |
| `AWS_S3_BUCKET` | ⚠️ | — | S3 bucket name |
| `REDIS_URL` | ⚠️ | — | OTP lockout + rate limit (in-memory fallback if absent) |
| `SENTRY_DSN` | ⚠️ | — | Error monitoring |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | ❌ | `15` | JWT access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | ❌ | `30` | Refresh token lifetime |
| `ADMIN_EMAIL` | ✅ production | ❌ empty | Admin login email (no default) |
| `ADMIN_PASSWORD` | ✅ production | ❌ empty | Admin login password (no default) |

### Mobile Apps (`app.config.ts` → `process.env.EXPO_PUBLIC_*`)

| Variable | Used By | Description |
|----------|---------|-------------|
| `EXPO_PUBLIC_API_URL` | Both apps | Backend base URL |
| `EXPO_PUBLIC_SUPABASE_URL` | Both apps | Supabase project URL |
| `EXPO_PUBLIC_SUPABASE_ANON_KEY` | Both apps | Supabase anonymous key |
| `EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY` | Both apps | Stripe card UI |
| `EXPO_PUBLIC_GOOGLE_MAPS_KEY` | Both apps | MapView + Directions |
| `EXPO_PUBLIC_SENTRY_DSN` | Both apps | Mobile error monitoring |
| `APP_VARIANT` | `frontend/` only | `rider` or `driver` (dual-variant app) |

### Admin Dashboard (`next.config.ts` → `process.env.NEXT_PUBLIC_*`)

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | Backend base URL |
| `NEXTAUTH_SECRET` | NextAuth session encryption |
| `NEXTAUTH_URL` | Admin dashboard deployment URL |
| `NEXT_PUBLIC_SENTRY_DSN` | Admin error monitoring |

---

## 17. Dependency Graph

### Import Resolution — TypeScript Path Aliases

```
rider-app/tsconfig.json:
  "@/*"       → rider-app/*           (local files)
  "@shared/*" → ../shared/*           (monorepo shared package)

driver-app/tsconfig.json:
  "@/*"          → driver-app/*       (local files)
  "@shared/*"    → ../shared/*        (monorepo shared package)
  "@components/*"→ ./components/*
  "@hooks/*"     → ./hooks/*
  "@styles/*"    → ./styles/*
  "@types/*"     → ./types/*

admin-dashboard/tsconfig.json:
  "@/*"       → admin-dashboard/src/* (local files only)

shared/tsconfig.json:
  extends driver-app/tsconfig.json
  node_modules resolution via driver-app/node_modules
```

### Package Dependency Tree (critical paths)

```
rider-app/package.json
  └─ expo ~54.0.0
      ├─ expo-router ~6.0.23
      ├─ expo-location (built-in)
      ├─ expo-notifications ~0.32.16
      ├─ expo-secure-store (built-in)
      ├─ expo-file-system (built-in)
      └─ expo-sharing (built-in)
  └─ react-native 0.81.5
      └─ react-native-maps 1.20.1
  └─ @react-native-firebase/app ^24.0.0
      ├─ @react-native-firebase/messaging ^24.0.0
      ├─ @react-native-firebase/crashlytics ^24.0.0
      └─ @react-native-firebase/app-check ^24.0.0
  └─ @stripe/stripe-react-native 0.50.3
  └─ @supabase/supabase-js ^2.95.3
  └─ zustand ^5.0.0

driver-app/package.json  (identical to rider-app + sharp ^0.34.5 for image resizing)

admin-dashboard/package.json
  └─ next 16.1.6
      └─ react 19.2.3 + react-dom 19.2.3
  └─ tailwindcss ^4
  └─ recharts ^3.8.1
  └─ leaflet 1.9.4 + react-leaflet 5.0.0
  └─ zustand ^5.0.12
  └─ @playwright/test ^1.44.0
      └─ @axe-core/playwright ^4.9.0
  └─ vitest ^4.1.3
```

---

## 18. Architecture Decision Records (ADRs)

| ADR | Decision | Status | Rationale |
|-----|----------|--------|-----------|
| ADR-001 | FastAPI over Django/Flask | Accepted | Async-first, Pydantic v2 native, auto OpenAPI docs, WebSocket support |
| ADR-002 | Expo SDK 54 (bare workflow) over pure React Native | Accepted | EAS Build, OTA updates, SDK module ecosystem |
| ADR-003 | Supabase over Firebase Firestore for primary DB | Accepted | PostgreSQL semantics, RLS, PostgREST, real-time built-in |
| ADR-004 | Zustand v5 over Redux / Jotai | Accepted | Minimal boilerplate, TypeScript-first, no Provider wrapping |
| ADR-005 | `@react-native-firebase/messaging` over Expo Notifications | Accepted | Full FCM lifecycle (background/killed state) — Expo Notifications cannot handle killed-state push |
| ADR-006 | expo-router (file-based) over React Navigation | Accepted | TypeScript typed routes, deep linking, web support |
| ADR-007 | Separate rider-app + driver-app over single app with role switch | Accepted | Smaller bundle, independent release cadence, cleaner permissions |
| ADR-008 | Stripe Connect over direct bank integration | Accepted | PCI compliance handled by Stripe, instant driver payout, international |
| ADR-009 | Python `Decimal` for all money | Accepted | IEEE 754 float errors unacceptable in financial transactions |
| ADR-010 | JWT (15 min) + opaque refresh token (30 day) | Accepted | Short JWT window limits blast radius of theft; opaque refresh enables revocation |
| ADR-011 | Redis for rate limiting and OTP lockout state | Accepted | In-memory fails on server restart; Redis persists state across deployments |
| ADR-012 | Optimistic locking (compare-and-swap) for ride acceptance | Accepted | Eliminates race condition without distributed locks; 409 + WebSocket notification to losing driver |
| ADR-013 | Haversine geofence in `driverStore` (client-side) | Accepted | Instant UX feedback without API round-trip; backend re-validates on `DRIVER_ARRIVED` event |
| ADR-014 | Playwright + axe-core for admin E2E + WCAG | Accepted | Single tool covers functional + accessibility; AODA compliance required |
| ADR-015 | Monorepo `shared/` package over npm workspace | Accepted | Simple path alias resolution; avoids npm link complexity for mobile |

---

## 19. Alignment Issues & Recommendations

### 🔴 Critical Misalignments (fix before production)

| # | Issue | Location | Recommendation |
|---|-------|----------|---------------|
| A1 | `backend/core/config.py` still has `JWT_SECRET = "your-strong-secret-key"` as default | `config.py:14` | Startup guard now prevents this in production, but the string literal should be removed entirely — replace default with `""` and document that env var is mandatory |
| A2 | `shared/package.json` has no version pinning — `"*"` for `@react-native-community/netinfo` and `react-native-worklets` | `shared/package.json` | Pin all shared dependencies to exact versions matching rider-app/driver-app to prevent silent version drift |
| A3 | `frontend/` is a dual-variant Expo app that duplicates rider-app + driver-app functionality | `frontend/app.config.ts` | Either: (a) migrate rider-app and driver-app to use frontend/ as the source of truth, or (b) deprecate frontend/ entirely — currently three separate apps exist serving the same purpose |
| A4 | `discovery/` mini-app shares the same bundle ID as driver-app (`com.spinr.driver`) | `discovery/app.config.ts` | Use a distinct bundle ID (`com.spinr.discovery`) or merge discovery into the driver-app onboarding flow |
| A5 | Python version mismatch: `ci.yml` pins `PYTHON_VERSION: '3.11'` but Dockerfile uses Python 3.12 | `.github/workflows/ci.yml:8` | Pin CI to `'3.12'` to match production Docker image |

### 🟡 High-Priority Improvements

| # | Issue | Location | Recommendation |
|---|-------|----------|---------------|
| B1 | `backend/routes/admin.py` is 133KB — largest file in the codebase | `routes/admin.py` | Refactor into sub-modules: `admin/stats.py`, `admin/drivers.py`, `admin/rides.py`, `admin/promotions.py` |
| B2 | `backend/routes/drivers.py` is 78KB and `rides.py` is 55KB | Both files | Apply same refactor — extract by business domain |
| B3 | `shared/store/authStore.ts` is shared but the admin dashboard has its own `admin-dashboard/src/store/authStore.ts` with different shape | Two separate files | Rename admin store to `adminAuthStore.ts` to prevent confusion; document that they are intentionally different (NextAuth vs JWT) |
| B4 | No `backend/utils/audit_logger.py` in the main branch (only exists in sprint branches) | `backend/utils/` | Confirm it is present on `main` — if not, the security logging from Sprint 2 was not merged |
| B5 | `agents/` directory contains Python AI automation agents but is not documented in any architecture doc | `agents/*.py` | Add to ARCHITECTURE.md; document which agents run automatically vs manually; ensure they are not running in production without review |
| B6 | `backend/sql/` and `backend/migrations/` both exist — unclear which is authoritative | `backend/sql/`, `backend/migrations/` | Consolidate into `backend/migrations/` only; use `migrate.py` CLI as single entry point |
| B7 | `.github/workflows/ci.yml` has smoke-test job blocked by `if: false` | `ci.yml:~394` | As soon as a deployment URL is available, change to `if: github.ref == 'refs/heads/main'` |

### 🟢 Value-Add Enhancements

| # | Enhancement | Effort | Value |
|---|------------|--------|-------|
| C1 | Add OpenAPI spec export (`GET /openapi.json`) + Swagger UI link to README | Low | Developer DX, client SDK generation |
| C2 | Add `scripts/install-hooks.sh` to auto-install `.claude/hooks/pre-commit` for new developers | Low | Every contributor gets security hooks automatically |
| C3 | Add `@react-native-community/netinfo` version to rider-app/driver-app `package.json` (currently only in `shared/`) | Low | Explicit version control |
| C4 | Add Codecov badge to README — CI already uploads coverage | Low | Visibility into test coverage trends |
| C5 | Replace `backend/tests_smoke_supabase.py` (ad-hoc script) with a proper pytest fixture | Medium | Integrates with CI coverage |
| C6 | Add `docker-compose.yml` for local development (backend + Redis + PostgreSQL) | Medium | Eliminates "works on my machine" issues |
| C7 | Add `backend/ruff.toml` to repo (CI references it but it may not exist) | Low | Ensures local `ruff` behaviour matches CI |
| C8 | Move `google-services.json` out of source control; use EAS secrets instead | Medium | Firebase config should not be in git (SEC-010 sister issue) |

---

## 20. Quick Reference Cheat Sheet

### Commands

```bash
# Backend
cd backend
uvicorn server:app --reload --port 8000
pytest tests/ -v --cov=. --cov-report=term-missing
ruff check . && ruff format .
python migrate.py --env development

# Rider App
cd rider-app
yarn start                      # Expo Go / dev server
yarn ios                        # iOS simulator
yarn android                    # Android emulator
yarn test                       # Jest unit tests
npx expo-doctor                 # Check Expo config health

# Driver App
cd driver-app
yarn start
yarn test

# Admin Dashboard
cd admin-dashboard
npm run dev                     # Next.js dev server (port 3000)
npm test                        # Vitest unit tests
npm run test:e2e                # Playwright E2E
npm run build                   # Production build

# EAS Builds
cd rider-app && eas build --platform ios --profile preview
cd driver-app && eas build --platform android --profile production

# Maestro Mobile E2E
maestro test .maestro/rider/01_login.yaml
maestro test .maestro/driver/
```

### File Lookup — "Where does X live?"

| I need to change... | Go to... |
|--------------------|---------|
| API base URL | `shared/config/spinr.config.ts` |
| JWT lifetime | `backend/core/config.py` → `ACCESS_TOKEN_EXPIRE_MINUTES` |
| OTP digit count | `backend/dependencies.py` → `generate_otp(k=6)` |
| CORS allowed origins | `backend/core/config.py` → `ALLOWED_ORIGINS` |
| Push notification handler | `driver-app/app/_layout.tsx` (module scope) |
| Ride acceptance logic | `backend/routes/drivers.py` → `accept_ride()` |
| Fare calculation | `backend/routes/fares.py` + `backend/utils/analytics.py` |
| "Arrived" geofence distance | `driver-app/store/driverStore.ts` → `arriveAtPickup()` |
| SOS button | `shared/components/SOSButton.tsx` |
| WebSocket events | `backend/socket_manager.py` + `backend/routes/websocket.py` |
| Stripe webhook | `backend/routes/webhooks.py` |
| Admin stats API | `backend/routes/admin.py` |
| Fleet map component | `admin-dashboard/src/components/driver-map.tsx` |
| Security event logging | `backend/utils/audit_logger.py` |
| Token refresh | `shared/api/client.ts` (401 interceptor) + `backend/routes/auth.py` |
| OTP lockout | `backend/routes/auth.py` → `check_otp_lockout()` |
| CI pipeline | `.github/workflows/ci.yml` |
| Pre-commit hook | `.claude/hooks/pre-commit` |
| Environment variables | `backend/core/config.py` (backend) / `app.config.ts` (mobile) |

### Port Map

| Service | Port |
|---------|------|
| FastAPI backend | 8000 |
| Admin dashboard (Next.js dev) | 3000 |
| Rider app (Expo) | 8081 |
| Driver app (Expo) | 8082 |
| PostgreSQL (local test) | 5432 |
| Redis | 6379 |

---

*Generated by Claude Code (claude-sonnet-4-6) — Spinr Tech Stack & File Mapping v1.0*  
*Authored with: vms — 2026-04-10*
