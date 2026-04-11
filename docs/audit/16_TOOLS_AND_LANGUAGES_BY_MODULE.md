# Spinr — Tools & Programming Languages by Module

**Date:** 2026-04-10  
**Purpose:** Concise per-module reference of every language, framework, library, and tool used in the Spinr platform  

---

## Module Map

```
spinr/
├── backend/              ← Python 3.12
├── rider-app/            ← TypeScript + React Native
├── driver-app/           ← TypeScript + React Native
├── admin-dashboard/      ← TypeScript + Next.js
├── shared/               ← TypeScript (consumed by rider + driver)
├── agents/               ← Python 3.12 (AI automation)
└── .github/workflows/    ← YAML (GitHub Actions CI/CD)
```

---

## 1. Backend (`backend/`)

**Language:** Python 3.12

| Category | Tool / Library | Version | What It Does in This Module |
|----------|---------------|---------|----------------------------|
| **Framework** | FastAPI | ≥ 0.115.0 | REST API + WebSocket routing, auto OpenAPI docs, Depends() injection |
| **ASGI Server** | Uvicorn | ≥ 0.30.0 | Runs FastAPI — handles HTTP and WebSocket connections |
| **Data Validation** | Pydantic v2 | ≥ 2.10.0 | All request/response models in `schemas.py`, settings in `core/config.py` |
| **Config Management** | pydantic-settings | ≥ 2.5.0 | Type-safe env var loading (`core/config.py`) |
| **Database Client** | supabase-py | ≥ 2.10.0 | PostgREST calls to Supabase PostgreSQL (`db.py`, `db_supabase.py`) |
| **Auth — JWT** | PyJWT | ≥ 2.8.0 | Sign + verify 15-minute access tokens (`dependencies.py`, `core/security.py`) |
| **Auth — Hashing** | bcrypt | ≥ 4.0.0 | Admin password hashing |
| **Auth — Firebase** | firebase-admin | ≥ 6.0.0 | Verify FCM tokens, App Check attestation, send push notifications |
| **Rate Limiting** | slowapi | ≥ 0.1.9 | Per-endpoint rate limits (`utils/rate_limiter.py`) |
| **Async HTTP** | httpx[http2] | ≥ 0.27.0 | Outbound API calls (Google Maps, Stripe, etc.) |
| **Async HTTP** | aiohttp | ≥ 3.9.0 | Supplementary async HTTP client |
| **WebSockets** | websockets | ≥ 12.0.0 | Real-time ride events (`socket_manager.py`, `routes/websocket.py`) |
| **Payments** | stripe | ≥ 9.0.0 | Payment intents, Connect payouts, webhook verification (`routes/payments.py`, `routes/webhooks.py`) |
| **SMS / OTP** | twilio | ≥ 9.0.0 | 6-digit OTP delivery (`sms_service.py`) |
| **Image Storage** | cloudinary | ≥ 1.40.0 | Driver document photos (`utils/cloudinary.py`) |
| **Object Storage** | boto3 | ≥ 1.34.0 | AWS S3 document cold storage |
| **Money Arithmetic** | `decimal` (stdlib) | 3.12 built-in | Exact fare calculation — no float rounding errors (`routes/fares.py`) |
| **Logging** | loguru | ≥ 0.7.0 | Structured logs across all routes |
| **Security Logging** | Custom (`audit_logger.py`) | In-repo | `log_security_event()` for auth, payment, ride events |
| **Error Monitoring** | sentry-sdk | ≥ 1.40.0 | Runtime exceptions + performance traces |
| **Geospatial** | Custom (`geo_utils.py`) | In-repo | Haversine distance, bounding box, nearby driver lookup |
| **Data Processing** | pandas | ≥ 2.0.0 | Analytics aggregation (`utils/analytics.py`) |
| **Data Processing** | numpy | ≥ 1.24.0 | Numerical computation for analytics |
| **AI / ML** | google-generativeai | ≥ 0.4.0 | Gemini AI — demand forecasting, dispute summarisation |
| **Maps / Directions** | google-api-python-client | ≥ 2.0.0 | Geocoding, Directions API for route polylines |
| **Email** | jinja2 | ≥ 3.1.0 | HTML email receipt templates (`utils/email_receipt.py`) |
| **Migrations** | Custom (`migrate.py`) | In-repo | Ordered SQL migration runner with rollback + `--dry-run` |
| **Testing** | pytest | ≥ 8.0.0 | 12+ unit tests across auth, rides, drivers, geo, DB |
| **Testing** | pytest-asyncio | ≥ 0.23.0 | Async test support (FastAPI route testing) |
| **Testing** | pytest-cov | ≥ 4.1.0 | Coverage reporting → Codecov |
| **Testing** | pytest-mock | ≥ 3.12.0 | Stripe, Twilio, Firebase mocking |
| **Linting** | ruff | CI-enforced | Python linting + formatting (enforced in `ci.yml`) |
| **Containerisation** | Docker (Python 3.12.9-slim) | Multi-stage | Non-root user `spinr`, health check, production image |

---

## 2. Rider App (`rider-app/`)

**Language:** TypeScript 5.9.2  
**Runtime:** React Native 0.81.5 (iOS + Android)  
**Build Platform:** Expo SDK 54 (bare workflow)

| Category | Tool / Library | Version | What It Does in This Module |
|----------|---------------|---------|----------------------------|
| **Framework** | Expo SDK | ~54.0.0 | Managed native modules, OTA updates, EAS build pipeline |
| **Language** | TypeScript | ~5.9.2 | Static typing across all screens, stores, and utilities |
| **UI Library** | React | 19.1.0 | Component rendering |
| **Mobile Runtime** | React Native | 0.81.5 | Native iOS/Android bridge |
| **Navigation** | expo-router | ~6.0.23 | File-based routing — each file in `app/` = a route |
| **State Management** | zustand | ^5.0.0 | `authStore` (tokens, user), `locationStore` (GPS coords) |
| **Maps** | react-native-maps | 1.20.1 | `MapView`, `Marker`, `Polyline` — pickup/dropoff + route display |
| **Push Notifications** | @react-native-firebase/messaging | ^24.0.0 | FCM token registration, foreground message handler |
| **App Integrity** | @react-native-firebase/app-check | ^24.0.0 | Device attestation on every API request |
| **Crash Reporting** | @react-native-firebase/crashlytics | ^24.0.0 | Non-fatal error capture, session logging |
| **Payments** | @stripe/stripe-react-native | 0.50.3 | Card UI, `confirmPayment()`, Apple/Google Pay |
| **Database Client** | @supabase/supabase-js | ^2.95.3 | Real-time ride status subscriptions |
| **Secure Storage** | expo-secure-store | SDK 54 | JWT + refresh token in iOS Keychain / Android Keystore |
| **Location** | expo-location | SDK 54 | GPS coordinates for pickup pin, nearby drivers |
| **Local Notifications** | expo-notifications | ~0.32.16 | In-app notification UI (foreground alerts) |
| **Network State** | @react-native-community/netinfo | * | Offline detection → `<OfflineBanner>` |
| **Safety** | `shared/components/SOSButton.tsx` | In-repo | Permanent floating SOS overlay — dials emergency + POST /disputes |
| **Accessibility** | eslint-plugin-jsx-a11y | CI-enforced | WCAG 2.1 AA lint on all TSX files |
| **Testing** | jest | ^29.7.0 | Unit tests (`rider-app/__tests__/`) |
| **Testing** | @testing-library/react-native | SDK 54 | Component render + interaction tests |
| **E2E Testing** | Maestro | YAML flows | `.maestro/rider/` — login flow, request + cancel ride |
| **Build** | EAS Build | Cloud | iOS + Android production builds |
| **Type Checking** | TypeScript strict | CI-enforced | `noImplicitAny`, `strictNullChecks` on every PR |

---

## 3. Driver App (`driver-app/`)

**Language:** TypeScript 5.9.2  
**Runtime:** React Native 0.81.5 (iOS + Android)  
**Build Platform:** Expo SDK 54 (bare workflow)

| Category | Tool / Library | Version | What It Does in This Module |
|----------|---------------|---------|----------------------------|
| **Framework** | Expo SDK | ~54.0.0 | Same as rider-app |
| **Language** | TypeScript | ~5.9.2 | Static typing |
| **UI Library** | React | 19.1.0 | Component rendering |
| **Mobile Runtime** | React Native | 0.81.5 | Native iOS/Android bridge |
| **Navigation** | expo-router | ~6.0.23 | File-based routing |
| **State Management** | zustand | ^5.0.0 | `driverStore` (online status, active ride, geofence), `documentStore`, `languageStore` |
| **Maps** | react-native-maps | 1.20.1 | `MapView`, `Polyline` (in-app navigation route), `CarMarker` |
| **Push (Background/Killed)** | @react-native-firebase/messaging | ^24.0.0 | `setBackgroundMessageHandler` (module scope), `getInitialNotification` (killed state), `onNotificationOpenedApp` (background state) |
| **App Integrity** | @react-native-firebase/app-check | ^24.0.0 | Device attestation |
| **Crash Reporting** | @react-native-firebase/crashlytics | ^24.0.0 | Non-fatal error reporting |
| **Payments** | @stripe/stripe-react-native | 0.50.3 | Payout method setup |
| **Database Client** | @supabase/supabase-js | ^2.95.3 | Realtime driver status sync |
| **Secure Storage** | expo-secure-store | SDK 54 | Token persistence |
| **Location** | expo-location | SDK 54 | Foreground + background GPS, 30s batch flush to `/drivers/location-batch` |
| **File System** | expo-file-system | SDK 54 | Write earnings CSV to temp file before sharing |
| **File Sharing** | expo-sharing | SDK 54 | Share earnings CSV via OS native share sheet |
| **Network State** | @react-native-community/netinfo | * | Offline detection |
| **Image Processing** | sharp | ^0.34.5 | Resize driver profile/document photos before upload |
| **Internationalisation** | Custom i18n | In-repo | `driver-app/i18n/en.json` + `fr.json` (Saskatchewan bilingual) |
| **WebSocket Reconnection** | Custom hook | In-repo | `useWebSocket.ts` — exponential backoff + jitter, AppState foreground trigger |
| **Geofence** | Custom Haversine | In-repo | `driverStore.arriveAtPickup()` — 150m radius guard; "Arrived" button disabled until within range |
| **Route Decoding** | Custom | In-repo | `decodePolyline()` in `driver/index.tsx` — decodes Google encoded polyline for `<Polyline>` |
| **Accessibility** | eslint-plugin-jsx-a11y | CI-enforced | WCAG 2.1 AA lint |
| **Testing** | jest | ^29.7.0 | Unit tests (`driver-app/__tests__/`) |
| **Testing** | @testing-library/react-native | SDK 54 | Component + hook tests |
| **E2E Testing** | Maestro | YAML flows | `.maestro/driver/` — login flow, go online |
| **Build** | EAS Build | Cloud | iOS + Android builds (EAS project: `1ed02cf4`) |

---

## 4. Admin Dashboard (`admin-dashboard/`)

**Language:** TypeScript 5.x  
**Framework:** Next.js 16.1.6 (App Router)  
**Runtime:** Node.js (server-side) + Browser (client-side)

| Category | Tool / Library | Version | What It Does in This Module |
|----------|---------------|---------|----------------------------|
| **Framework** | Next.js | 16.1.6 | SSR + App Router, API routes, middleware auth guard |
| **Language** | TypeScript | ^5 | Static typing across all pages, components, and lib |
| **UI Library** | React + React DOM | 19.2.3 | Component rendering |
| **Styling** | Tailwind CSS | ^4 | Utility-first styling across all components |
| **Component Library** | Shadcn UI (Radix-based) | Latest | 30+ accessible UI primitives: Button, Card, Dialog, Table, Toast, Select, Badge |
| **State Management** | zustand | ^5.0.12 | `adminAuthStore` — NextAuth session state |
| **Charts** | recharts | ^3.8.1 | Earnings charts, ride volume graphs, revenue analytics |
| **Fleet Map** | leaflet + react-leaflet | 1.9.4 / 5.0.0 | Real-time driver location map |
| **Geofence Editor** | leaflet-draw | ^1.0.4 | Draw + edit zone boundaries on fleet map |
| **Demand Heatmap** | leaflet.heat | ^0.2.0 | Ride density / cancellation heatmap layer |
| **PDF Export** | jspdf | ^4.2.1 | Export admin reports as PDF |
| **CSV Export** | Custom (`lib/export-csv.ts`) | In-repo | Driver/ride data export |
| **API Client** | Custom (`lib/api.ts`) | In-repo | Typed fetch wrapper for all backend calls |
| **Unit Testing** | vitest | ^4.1.3 | 12 component tests — RideCard, DriverCard, StatsPanel, forms |
| **E2E Testing** | @playwright/test | ^1.44.0 | 10 browser tests — login, auth, all 4 dashboard routes |
| **Accessibility Testing** | @axe-core/playwright | ^4.9.0 | WCAG 2.1 AA scan on every PR (wcag2a, wcag2aa, wcag21aa) |
| **E2E Readiness** | wait-on | ^7.2.0 | Waits for Next.js server before Playwright runs |
| **Type Checking** | TypeScript strict | CI-enforced | Strict mode on every PR |
| **Linting** | eslint (Next.js config) | ^9 | Code quality + jsx-a11y rules |

---

## 5. Shared Package (`shared/`)

**Language:** TypeScript  
**Consumed by:** rider-app + driver-app via `@shared/*` path alias

| Category | Tool / Library | Version | What It Does in This Module |
|----------|---------------|---------|----------------------------|
| **State** | zustand | (from apps) | `authStore` + `locationStore` — shared between rider and driver |
| **API Client** | Custom (`api/client.ts`) | In-repo | Bearer token injection, `X-Request-ID`, 401 auto-refresh interceptor |
| **Caching** | Custom LRU (`cache/index.ts`) | In-repo | TTL-aware in-memory cache for `cachedClient.ts` |
| **Firebase** | @react-native-firebase/messaging | (from apps) | `setBackgroundMessageHandler`, `getInitialNotification`, `onNotificationOpenedApp` |
| **Firebase Config** | `config/firebaseConfig.ts` | In-repo | Firebase project config object loaded from env vars |
| **Supabase Client** | @supabase/supabase-js | (from apps) | Singleton client in `config/supabase.ts` |
| **Maps** | react-native-maps | (from apps) | `AppMap.tsx` wrapper (native) + `AppMap.web.tsx` (Leaflet for web) |
| **Networking** | @react-native-community/netinfo | * | `OfflineBanner.tsx` — shows "No connection" when offline |
| **Error Capture** | sentry-sdk | (from apps) | `ErrorBoundary.tsx` wraps root — captures uncaught renders |
| **Logging** | Custom (`utils/logger.ts`) | In-repo | Structured `debug/info/warn/error` with Sentry breadcrumbs |
| **Language** | TypeScript | (from apps) | All files are `.ts` / `.tsx` |

---

## 6. AI Agents (`agents/`)

**Language:** Python 3.12

| File | Tool / Library | What It Does |
|------|---------------|-------------|
| `base_agent.py` | Anthropic Claude API | Base class for all agents — prompt construction, response handling |
| `backend_agent.py` | Claude API | Analyses and generates backend (FastAPI) code |
| `frontend_agent.py` | Claude API | Analyses and generates frontend (React Native/Next.js) code |
| `security_agent.py` | Claude API | Security review, vulnerability detection, OWASP mapping |
| `code_reviewer.py` | Claude API | Code quality review, PR analysis |
| `tester.py` | Claude API | Generates unit and integration tests |
| `documenter.py` | Claude API | Generates technical documentation |
| `deployer.py` | Claude API | Deployment orchestration |
| `orchestrator.py` | Claude API | Multi-agent coordination |
| `knowledge_base.py` | Claude API | Spinr platform knowledge retrieval |
| `registry.py` | Python | Agent registration and discovery |
| `cli.py` | typer | CLI entry point for running agents manually |
| `examples.py` | Python | Usage examples |

**Key dependencies:** `anthropic` SDK, `typer`, `rich` (CLI output), `pyyaml`

---

## 7. CI/CD Pipeline (`.github/workflows/`)

**Language:** YAML  
**Platform:** GitHub Actions

| Job | Tool | Language Tested | What It Checks |
|-----|------|----------------|---------------|
| `secrets-scan` | TruffleHog | All | Blocks merge if any verified secret found in diff |
| `container-scan` | Trivy | Docker | Exits non-zero on CRITICAL/HIGH CVE in backend image |
| `backend-lint` | ruff | Python | Code style + quality (`ruff.toml` config) |
| `backend-test` | pytest + pytest-cov | Python | 12+ unit tests, coverage report → Codecov |
| `frontend-test` | Jest | TypeScript (RN) | Rider app unit tests |
| `driver-test` | Jest | TypeScript (RN) | Driver app unit tests |
| `admin-test` | Vitest | TypeScript (Next.js) | Admin dashboard unit tests |
| `admin-build` | Next.js build | TypeScript | Production build check |
| `rider-type-check` | TypeScript strict | TypeScript (RN) | Type errors in rider-app |
| `driver-type-check` | TypeScript strict | TypeScript (RN) | Type errors in driver-app |
| `e2e-test` | Playwright + axe-core | TypeScript | 10 E2E + WCAG 2.1 AA tests on admin dashboard |
| `a11y-lint` | eslint-plugin-jsx-a11y | TypeScript (TSX) | Accessibility lint on all mobile + web components |
| `smoke-test` | curl / custom | HTTP | Post-deploy health check (`if: false` until deployment URL set) |

---

## 8. Database & Infrastructure

| Layer | Tool | Language / Type | What It Does |
|-------|------|----------------|-------------|
| **Primary Database** | Supabase (PostgreSQL 15) | SQL | All rides, users, drivers, payments, OTP hashes, refresh tokens |
| **Real-Time** | Supabase Realtime | WebSocket | Live ride status, driver location subscriptions (apps) |
| **Row Level Security** | Supabase RLS | SQL policies | Per-user data isolation enforced at DB layer |
| **Migrations** | `backend/migrate.py` | Python CLI | Ordered SQL migration runner |
| **In-App Cache** | Redis | — | OTP lockout state, sliding-window rate limits, fare cache |
| **Object Storage** | Cloudinary / AWS S3 | — | Driver document photos (Cloudinary primary, S3 cold) |
| **Container** | Docker (Python 3.12.9-slim) | Dockerfile | Multi-stage build, non-root user, health check |
| **Mobile Builds** | EAS (Expo Application Services) | Cloud YAML | iOS App Store + Google Play builds |
| **Backend Deploy** | Railway / Render | — | Single Docker container hosting |
| **Admin Deploy** | Vercel | — | Next.js admin dashboard hosting |
| **Dependency Updates** | Dependabot | YAML config | Weekly PRs for pip, npm (×4), GitHub Actions |
| **Coverage Reports** | Codecov | — | PR comments with test coverage delta |
| **Error Monitoring** | Sentry | SDK in each surface | Runtime errors + performance across all 5 surfaces |

---

## 9. Developer Tooling (Local)

| Tool | Language / Type | What It Does |
|------|----------------|-------------|
| `.claude/hooks/pre-commit` | Bash | 5-check security gate before every commit: secrets scan, forbidden files, PII in logs, branch naming, float money arithmetic |
| `.claude/settings.json` | JSON | Claude Code model pin, allow/deny permission matrix for all Bash/Write operations |
| `.claude/commands/*.md` | Markdown | `/commit`, `/pr`, `/review`, `/start`, `/status` slash-command skills |
| `ruff` | Python | Local linting matches CI — run `ruff check . && ruff format .` |
| `typescript` strict | TypeScript | Run `npx tsc --noEmit` in any app directory |
| `eslint-plugin-jsx-a11y` | TypeScript / TSX | Accessibility lint locally before push |
| `expo-doctor` | CLI | Checks Expo SDK health, plugin compatibility |
| `maestro` | YAML | Run mobile E2E flows locally against simulator |
| `playwright` | TypeScript | Run `npm run test:e2e` in admin-dashboard |
| `pytest` | Python | Run `pytest tests/ -v` in backend |

---

## Summary Matrix — Language × Module

| Module | Primary Language | Secondary Language | Key Framework |
|--------|----------------|-------------------|--------------|
| `backend/` | **Python 3.12** | SQL (Supabase) | FastAPI + Uvicorn |
| `rider-app/` | **TypeScript 5.9** | — | Expo SDK 54 + React Native 0.81.5 |
| `driver-app/` | **TypeScript 5.9** | — | Expo SDK 54 + React Native 0.81.5 |
| `admin-dashboard/` | **TypeScript 5.x** | CSS (Tailwind) | Next.js 16 + React 19 |
| `shared/` | **TypeScript 5.9** | — | (consumed by mobile apps) |
| `agents/` | **Python 3.12** | — | Anthropic Claude API |
| `.github/workflows/` | **YAML** | Bash (inline) | GitHub Actions |
| `docs/` | **Markdown** | — | — |
| `.maestro/` | **YAML** | — | Maestro mobile UI automation |
| `backend/migrations/` | **SQL** | Python (runner) | `migrate.py` CLI |

---

*Generated by Claude Code (claude-sonnet-4-6) — Spinr Tools & Languages by Module v1.0*  
*Authored with: vms — 2026-04-10*
