# 01 — Executive Summary & Architecture

> **Read time:** ~10 min
> **Audience:** CTO, Eng Lead, Product
> **Audit branch:** `claude/audit-production-readiness-UQJSR`

---

## TL;DR (one paragraph)

Spinr is a full-stack ride-share platform (rider app, driver app, admin dashboard, FastAPI backend, Supabase/PostGIS DB) with **strong engineering hygiene** — clean layering, Dependabot, CI security scans, bcrypt-12, structured logs, magic-byte file validation, and Stripe signature verification. However, it has **10 P0 blockers** that will cause data loss, payment duplication, OTP abuse, or full outages under real traffic. The most dangerous class of defects sits at the **infra / real-time / payment boundary**: `min_machines_running = 0` silently halts the surge engine, scheduled dispatcher, payment retry, and document expiry jobs; Stripe webhooks have no idempotency key so every retry double-pays; rate limiting is per-instance in memory; WebSocket sessions are per-process. None require rewrites — all are 1–3 day fixes — but they must land **before** first paying user. Overall score: **C+**. Recommended: 4–6 weeks of focused remediation before public launch.

---

## System architecture (textual map)

```
                      ┌─────────────────────────────────────────┐
                      │              End users                  │
                      │  Riders (iOS/Android)  Drivers (iOS/And)│
                      │             Admins (web)                │
                      └──────────────┬──────────────┬───────────┘
                                     │              │
                          HTTPS + WS │              │ HTTPS
                                     ▼              ▼
                   ┌─────────────────────────────────────────┐
                   │   FastAPI backend (Fly.io yyz region)   │
                   │   - 287+ endpoints in /api/v1 + /api    │
                   │   - Uvicorn, multi-stage Docker, non-root│
                   │   - Background tasks: surge, dispatcher, │
                   │     payment-retry, doc-expiry, subs-expiry│
                   └──┬──────────┬──────────┬──────────┬─────┘
                      │          │          │          │
                      ▼          ▼          ▼          ▼
                ┌──────────┐ ┌─────────┐ ┌───────┐ ┌────────┐
                │ Supabase │ │ Stripe  │ │Twilio │ │SendGrid│
                │ (Postgres│ │(payment)│ │ (SMS) │ │(email) │
                │ +PostGIS)│ │         │ │       │ │        │
                └──────────┘ └─────────┘ └───────┘ └────────┘
                      │
                      ▼
                ┌──────────────────────────────────────┐
                │ Supabase Storage (docs, vehicle pics)│
                │ Cloudinary (profile avatars)         │
                │ Firebase (FCM push, App Check, Auth  │
                │ ID token, Crashlytics)               │
                └──────────────────────────────────────┘

  Admin dashboard (Vercel / Next.js 16.2.3, React 19)
  Rider app (Expo SDK 54, React Native 0.81.5)
  Driver app (Expo SDK 55, React Native 0.81.5)  ◀── version drift
```

**Critical observation:** the backend is a **single-process monolith** that owns (a) sync HTTP requests, (b) WebSocket fan-out to drivers/riders, (c) in-memory rate-limit counters, and (d) five background loops. With `min_machines_running = 0`, any machine idle kills all four of the non-HTTP concerns.

---

## Tech stack inventory

| Layer | Tech | Version | Purpose | Risk |
|---|---|---|---|---|
| Backend | FastAPI | 0.117.1 | HTTP/WS API | Low |
| Lang | Python | 3.12.9 | — | Low |
| DB | Supabase (Postgres + PostGIS) | 15.x | Primary store | Medium — RLS gaps |
| Auth | JWT (HS256) + Firebase ID token | 30d exp | Session | **High — no refresh/revoke** |
| Cache/RL | slowapi `memory://` | 0.1.9 | Rate limiting | **High — per-instance** |
| Payments | Stripe | 13.1.0 | Cards, subs | **High — no idempotency** |
| SMS | Twilio | 9.9.1 | OTP | Medium |
| Email | SendGrid | 6.12.5 | Receipts | Low |
| Images | Cloudinary | 1.44.1 | Avatars | Low |
| Logs | Loguru JSON | 0.7.3 | Structured logs | Low |
| Errors | Sentry SDK | 2.44.2 (optional) | APM | Medium — optional in prod |
| Rider/Driver | Expo | 54 / **55** | Mobile | **Medium — version drift** |
| RN | React Native | 0.81.5 | — | Low |
| RN state | Zustand | 5.x | Client state | Low |
| Admin | Next.js | 16.2.3 | Dashboard | Low |
| React | React | 19.1.0 | UI | Low |
| E2E | Playwright + axe | latest | A11y + flows | Low |
| Infra | Fly.io (yyz) | — | Primary | **High — cold start halts jobs** |
| Infra alt | Render | — | Fallback | Medium |
| Hosting web | Vercel | — | Admin + web | Low |
| Mobile build | EAS Build | — | iOS/Android | Low |
| Scans | Trivy + TruffleHog | — | CI security | Low |
| Deps | Dependabot | — | Updates | Low |

---

## Module & folder reference

### Top-level
```
/                  Project root, lots of legacy *.md reports
  backend/         FastAPI monolith
  admin-dashboard/ Next.js 16 admin UI
  rider-app/       Expo SDK 54 rider mobile app
  driver-app/      Expo SDK 55 driver mobile app  ← SDK drift
  frontend/        Legacy web stub (unused?)
  shared/          Cross-app TS/JS helpers
  scripts/         Ops scripts
  docs/            Documentation (this audit lives here)
  tests/           Cross-cutting integration tests
  fly.toml         Primary deploy config
  render.yaml      Backup deploy config
```

### `backend/`
```
server.py              FastAPI app bootstrap, router mount
dependencies.py        Auth (JWT + Firebase), RBAC, get_current_user
db.py                  Supabase client wrapper
supabase_client.py     Singleton client factory
supabase_rls.sql       RLS policies (INCOMPLETE — only 10 tables)
supabase_schema.sql    Canonical schema
schemas.py             Pydantic models
validators.py          Shared input validators
socket_manager.py      In-memory WS registry (NOT distributed)
settings_loader.py     Runtime settings from DB
sms_service.py         Twilio OTP
features.py            Feature flag loader
onboarding_status.py   Driver onboarding state machine
documents.py           Driver doc upload + magic-byte check
make_admin.py          CLI: promote a user to admin
uploads/               Local dev uploads (not used in prod)

  core/
    config.py          Pydantic Settings (env vars, dev defaults)
    lifespan.py        Startup/shutdown — has DEAD CODE bug
    middleware.py      Prod config validator, CORS, error handler

  routes/              22 top-level routers
    auth.py            OTP, JWT issue, session mgmt
    users.py           Rider profile
    rides.py           Request / accept / complete / cancel
    drivers.py         Driver profile, go online/offline
    payments.py        Stripe intents, cards
    webhooks.py        Stripe webhooks (NO IDEMPOTENCY)
    websocket.py       /ws/{role} endpoint
    addresses.py       Saved places, geocoding
    disputes.py        Rider/driver disputes
    fares.py           Fare estimate
    fare_split.py      Split-fare between riders
    favorites.py       Favorite drivers
    loyalty.py         Points
    notifications.py   In-app + FCM
    promotions.py      Coupons
    quests.py          Driver quests/challenges
    settings.py        App settings
    wallet.py          Driver earnings wallet
    corporate_accounts.py  B2B accounts
    admin/             Admin-only sub-routers (17 files)
      analytics.py, auth.py, documents.py, drivers.py, faqs.py,
      maintenance.py, messaging.py, promotions.py, rides.py,
      service_areas.py, settings.py, staff.py, subscriptions.py,
      support.py, users.py, vehicle_fleet.py

  utils/               Domain helpers + background loops
    surge_engine.py          Background task, 2-min recalc
    scheduled_rides.py       Background task, 60s tick
    payment_retry.py         Background task, 5-min tick
    document_expiry.py       Background task, 12-hour tick
    demand_forecast.py       Analytics
    quest_tracker.py         Driver quest progress
    rate_limiter.py          slowapi wrapper (memory://)
    error_handling.py        SpinrException hierarchy
    email_receipt.py         SendGrid
    cloudinary.py            Image upload
    password.py              Bcrypt + sha256 migration
    analytics.py             Event ingest

  migrations/          23 .sql files — DUPLICATE prefix 10
  sql/                 Ad-hoc SQL
  tests/               pytest (80% target)
```

### `admin-dashboard/src/`
```
app/            Next.js App Router pages
components/     UI components
hooks/          React hooks (auth, data)
lib/            API client, helpers
store/          Zustand stores
middleware.ts   Next.js middleware (auth gate)
e2e/            Playwright specs
playwright/     Playwright config
__tests__/      Vitest unit tests
```

### `rider-app/app/` (Expo Router)
Screens include: `index`, `login`, `otp`, `profile-setup`, `search-destination`, `pick-on-map`, `ride-options`, `payment-confirm`, `ride-status`, `driver-arriving`, `driver-arrived`, `ride-in-progress`, `ride-completed`, `rate-ride`, `report-safety`, `chat-driver`, `emergency-contacts`, `fare-split`, `legal`, `manage-cards`, `privacy-settings`, `promotions`, `saved-places`, `scheduled-rides`, `settings`, `support`, `become-driver`, `ride-details`, `(tabs)/…`.

### `driver-app/app/`
Mirror of rider screens with driver-specific additions (onboarding, documents, earnings, heatmap, quests, subscription, shift management).

---

## Core user flows (critical paths)

### Flow 1 — Rider requests a ride
```
rider-app  ─POST /api/v1/rides/request──▶  routes/rides.py
                                           ├─ validate service area
                                           ├─ price from fare_configs + surge
                                           ├─ insert rides row status=searching
                                           └─ socket_manager.broadcast_to_drivers()
                                              └─ each driver in area gets WS offer
```
**Risk:** `socket_manager` is per-process. Driver connected to machine B never sees offer from machine A.

### Flow 2 — Driver accepts
```
driver-app ─WS "accept_ride"──▶  websocket.py
                                 ├─ atomic UPDATE rides
                                 │  SET driver_id, status='driver_assigned'
                                 │  WHERE id=? AND status='searching'
                                 ├─ push notify rider (FCM)
                                 └─ broadcast acceptance to other drivers
```
**Risk:** no row-level advisory lock; relies on atomic UPDATE WHERE — OK but fragile if columns change.

### Flow 3 — Payment capture
```
ride complete ──▶ routes/rides.py: mark 'completed'
              ├─ create Stripe PaymentIntent (off-session)
              └─ return 200

Stripe retries webhook if 2xx not returned in 20s:
webhooks.py ◀── payment_intent.succeeded
              ├─ verify signature ✅
              ├─ mark ride paid          ◀── NO IDEMPOTENCY
              ├─ credit driver wallet     ◀── NO IDEMPOTENCY
              └─ push receipt             ◀── duplicate pushes
```
**Risk:** P0 — double wallet credit on Stripe retry.

### Flow 4 — Dispute
```
rider/driver ─POST /api/v1/disputes─▶ disputes.py
                                     └─ insert disputes row (no RLS)
                                     └─ no admin notification
```
**Risk:** `disputes` table has zero RLS. Missing admin notification channel.

---

## Cross-component dependency graph (hot spots)

| Hot dependency | Importers | Fault-domain blast radius |
|---|---|---|
| `backend/db.py` | nearly all routes | If Supabase down, entire API returns 500s (no circuit breaker) |
| `backend/dependencies.py::get_current_user` | every auth'd route | 30-day JWT means revocation gap |
| `backend/socket_manager.py` | websocket.py, rides.py | Per-process; single source of truth |
| `backend/utils/surge_engine.py` | startup loop | Stops when machine stops (min=0) |
| `backend/utils/rate_limiter.py` | auth.py, sensitive routes | Per-process — limits × N machines |
| `backend/routes/webhooks.py` | Stripe | Non-idempotent |
| `supabase_rls.sql` | DB directly | 20+ tables unprotected |

---

## Headline verdict by pillar

| Pillar | Status | Must-fix before launch? |
|---|---|---|
| Auth correctness | Mostly good | Add refresh token + revocation list |
| Data integrity | Mixed | Finish RLS + add migration framework |
| Payments | At-risk | Idempotency + reconciliation job |
| Real-time | Broken at >1 machine | Move to Redis pub/sub or stateful affinity |
| Infra | Broken at idle | `min_machines_running ≥ 1` + separate worker |
| Observability | Weak | Make Sentry mandatory, add `/metrics` |
| Security hygiene | Good | Add headers, rotate secrets |
| Compliance | Absent | DPA, retention, privacy-by-design docs |

---

*Continue to → [02_SECURITY_AUDIT.md](./02_SECURITY_AUDIT.md)*
