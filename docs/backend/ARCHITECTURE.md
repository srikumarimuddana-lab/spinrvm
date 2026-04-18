# Spinr Backend — High-Level Architecture

**Purpose:** Bird's-eye map of the Spinr backend — tech stack, topology, request lifecycle, cross-cutting concerns, and where each domain lives.

**Audience:** Engineers onboarding to the codebase. Pair with the per-domain docs under `docs/backend/` and with `docs/CORPORATE_B2B.md` (which covers the corporate B2B sub-system in depth).

**Last updated:** 2026-04-17

---

## 1. Stack at a glance

| Layer | Tech |
|-------|------|
| App framework | FastAPI (Python 3.11+) |
| Validation | Pydantic v2 |
| Database | Supabase (Postgres) — accessed via `supabase-py` service-role client |
| Auth | Firebase ID token (mobile) + short-lived HS256 JWT (rider/driver/admin) + opaque rotating refresh tokens |
| Background queue | `asyncio.create_task` loops, spawned from `core/lifespan.py` |
| Cache / pubsub | Redis (`REDIS_URL`) — rate limiter, OTP lockout, fare cache, WebSocket fan-out |
| Realtime | WebSockets (`routes/websocket.py`) + Redis pub/sub (`utils/ws_pubsub.py`) |
| Payments | Stripe (PaymentIntents, SetupIntents, Connect for driver payouts) |
| SMS | Twilio (console fallback in dev) |
| Push | Firebase Cloud Messaging |
| File storage | Supabase Storage (`STORAGE_BUCKET=driver-documents`), Cloudinary (user uploads) |
| Observability | Loguru JSON → stderr (Railway captures), optional Sentry (10% traces/profiles) |
| Hosting | Railway (auto-deploy from `main`) |

---

## 2. Topology — single process, multi-replica

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          Railway deployment                              │
│                                                                          │
│  ┌───────────────┐   ┌───────────────┐          ┌───────────────┐        │
│  │  Replica N    │   │  Replica N+1  │   ...    │  Replica N+K  │        │
│  │  (uvicorn)    │   │  (uvicorn)    │          │  (uvicorn)    │        │
│  └──────┬────────┘   └──────┬────────┘          └──────┬────────┘        │
│         │                   │                          │                 │
│  ┌──────┴───────────────────┴──────────────────────────┴────────┐        │
│  │                        Shared Redis                          │        │
│  │   • rate-limit counters        • fare cache (5 min TTL)      │        │
│  │   • OTP failure windows        • WS pub/sub channel          │        │
│  └──────────────────────────────────────────────────────────────┘        │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────┐        │
│  │                  Supabase (Postgres)                         │        │
│  │   • All durable state (users, rides, wallets, ...)           │        │
│  │   • RLS on mobile-facing tables; service-role key bypasses   │        │
│  └──────────────────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────────────┘

External:
  Firebase Auth   Stripe (+webhooks)   Twilio SMS   FCM Push   Cloudinary
```

**Key invariants:**
- **Single process** per replica (uvicorn); no worker queues outside the process.
- **Stateless request path** — all state goes through Supabase or Redis. Safe to scale horizontally.
- **WebSocket fan-out** goes through Redis pub/sub (`spinr:ws:dispatch`) so a driver on replica A can message a rider on replica B. Falls back to local-only delivery if Redis is unavailable (dev), which silently drops cross-replica messages in prod — `WS_REDIS_URL` is required in `ENV=production`.
- **Background tasks** run on every replica unless explicitly guarded. That is fine today because every loop reads DB state as its source of truth and writes are either idempotent (webhook idempotency table, `reminder_sent` flags) or atomic (`claim_driver_atomic`). If you add a new loop, verify it will not double-fire across replicas.

---

## 3. Boot sequence (`core/lifespan.py`)

```
1. init_database()             → probe users table via run_sync; blocks startup in prod on failure
2. attach db client → app.state.db
3. spawn 7 background loops (asyncio.create_task):
     • subscription_expiry        (6 h)  – push for Spinr Pass expiring <24 h
     • surge_engine               (2 m)  – demand/supply → service_areas.surge_multiplier
     • scheduled_dispatcher       (60 s) – dispatch scheduled rides + 10-min reminders
     • payment_retry              (5 m)  – retry failed Stripe PaymentIntents
     • document_expiry            (12 h) – push for driver docs expiring <7 d
     • corporate_autotopup        (10 m) – off-session Stripe charge for corporate wallets
     • corporate_low_balance      (1 h)  – email nudge for corporate wallets w/ auto-topup off
4. ws_pubsub.start()            → connect Redis, subscribe "spinr:ws:dispatch"
5. Sentry init                  → only if SENTRY_DSN is set
```

Shutdown order is the reverse: stop ws_pubsub → cancel loops → db cleanup. Missing a step historically led to zombie tasks clinging to closed DB connections.

---

## 4. Request lifecycle

```
HTTP request
  │
  ▼
CORSMiddleware                   (ALLOWED_ORIGINS env; rejects "*" in prod)
  │
  ▼
SecurityHeadersMiddleware        (X-Frame-Options, HSTS prod-only, CSP)
  │
  ▼
RelativeRedirectMiddleware       (rewrite absolute Location → relative)
  │
  ▼
SlowAPI RateLimitMiddleware      (Redis-backed counters; per-route overrides)
  │
  ▼
FastAPI router                   (server.py mounts ~25 routers)
  │
  ▼
Dependencies
  • get_current_user             (Firebase ID token → JWT fallback → admin claim path)
  • get_admin_user               (role in {admin, super_admin, operations, support, finance, custom})
  │
  ▼
Route handler (routes/**)
  • Pydantic validates input
  • Calls services/ or db_supabase directly
  • Returns Pydantic model or dict
  │
  ▼
Exception handlers               (SpinrException → schema; validation → 422; HTTP → JSON w/ X-Request-ID)
```

### WebSocket lifecycle (`routes/websocket.py`)

```
WS /ws/{client_type}/{client_id}
  │
  ├─ accept
  ├─ first message: {type: "auth", token}      → verify Firebase, fall back to JWT
  ├─ register connection_key = "{type}_{user_id}"  (e.g. "driver_abc123")
  ├─ send {type: "auth_success"}
  ├─ spawn 30 s ping heartbeat
  └─ loop: handle messages (GPS, chat, status); rate-limited 30 msg/s, 64 KB max
```

Outbound server → client uses `ConnectionManager.send_personal_message`, which publishes to Redis. Every replica has a subscriber that delivers locally iff the target client is connected to that replica. See `RIDES_AND_DISPATCH.md` for the message catalog.

---

## 5. Domain map — where to look for what

```
┌───────────────────────────────────────────────────────────────────────────┐
│  IDENTITY & AUTH                                                          │
│  routes/auth.py • routes/admin/auth.py • routes/users.py • routes/admin/  │
│  staff.py • routes/admin/users.py • dependencies.py • utils/password.py   │
│  utils/refresh_tokens.py • utils/crypto.py • utils/rate_limiter.py        │
│  sms_service.py                                                           │
│  → See AUTH_AND_USERS.md                                                  │
├───────────────────────────────────────────────────────────────────────────┤
│  RIDES • DISPATCH • DRIVERS                                               │
│  routes/rides.py • routes/drivers.py • routes/fares.py • routes/fare_     │
│  split.py • routes/websocket.py • routes/admin/drivers.py • routes/admin/ │
│  rides.py • services/dispatch_service.py • services/fare_service.py       │
│  socket_manager.py • utils/ws_pubsub.py • utils/surge_engine.py           │
│  utils/scheduled_rides.py • utils/document_expiry.py • geo_utils.py       │
│  → See RIDES_AND_DISPATCH.md                                              │
├───────────────────────────────────────────────────────────────────────────┤
│  WALLET • PAYMENTS • LOYALTY • PROMOTIONS                                 │
│  routes/wallet.py • routes/payments.py • routes/loyalty.py • routes/      │
│  quests.py • routes/promotions.py • routes/disputes.py • routes/          │
│  favorites.py • routes/addresses.py • routes/notifications.py • routes/   │
│  webhooks.py • routes/admin/wallet.py • routes/admin/promotions.py        │
│  routes/admin/subscriptions.py • routes/admin/messaging.py • utils/       │
│  payment_retry.py • utils/email_receipt.py • utils/cloudinary.py          │
│  → See WALLET_AND_PAYMENTS.md                                             │
├───────────────────────────────────────────────────────────────────────────┤
│  CORPORATE B2B                                                            │
│  routes/corporate_accounts.py • routes/corporate_wallet.py • services/    │
│  corporate_wallet_service.py • utils/corporate_autotopup.py • utils/      │
│  corporate_low_balance.py • migrations/27_corporate_b2b_v1.sql • 28_      │
│  corporate_wallet_rpc.sql                                                 │
│  → See docs/CORPORATE_B2B.md (dedicated, comprehensive reference)         │
├───────────────────────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE • PLATFORM                                                │
│  server.py • core/lifespan.py • core/middleware.py • core/config.py       │
│  db_supabase.py • supabase_client.py • schemas.py • validators.py         │
│  utils/redis_client.py • utils/rate_limiter.py • utils/error_handling.py  │
│  utils/ws_pubsub.py • features.py • migrations/*.sql                      │
│  → See INFRASTRUCTURE.md                                                  │
├───────────────────────────────────────────────────────────────────────────┤
│  ADMIN / OPS                                                              │
│  routes/admin/* (analytics, monitoring, maintenance, faqs, settings,      │
│  support, documents, drivers, rides, users, staff, wallet, promotions,    │
│  subscriptions, messaging)                                                │
│  → See ADMIN_AND_OPS.md                                                   │
└───────────────────────────────────────────────────────────────────────────┘
```

Quick function/class lookup index: `REFERENCE.md`.

---

## 6. Cross-cutting concerns

| Concern | Location | How it works |
|---------|----------|--------------|
| **CORS** | `core/middleware.py` | `ALLOWED_ORIGINS` env; wildcard rejected in prod. |
| **Security headers** | `core/middleware.py` | X-Frame-Options, X-Content-Type-Options, HSTS (prod only), CSP. |
| **Auth** | `dependencies.py` | Firebase ID token first, then JWT; admin tokens have `role+email+modules` claims and skip DB lookup. Role is always re-read from DB for rider/driver tokens — forged `role=super_admin` in a rider JWT cannot escalate. |
| **Rate limiting** | `utils/rate_limiter.py` + SlowAPI | Redis storage (`RATE_LIMIT_REDIS_URL`; memory:// only in dev). Per-route overrides (OTP 3/min, login 5/min, ride create 10/min, driver location 60/min). |
| **OTP brute-force** | `utils/rate_limiter.py` (SEC-008) | `redis_incr` on `otp:failures:{phone}` in a sliding window; lockout 24 h after 5 failures. |
| **Errors** | `utils/error_handling.py` | `SpinrException` hierarchy with `ErrorCode` enum. All 4xx/5xx carry `X-Request-ID` (UUID hex[0:12]) for log correlation. |
| **Logging** | `server.py` + Loguru | JSON to stderr, captured by Railway. Never log secrets. |
| **DB** | `db_supabase.py` | 66-ish helpers wrapping Supabase sync client via `run_sync()` (retries once on h2 GOAWAY). RLS active; service role bypasses on the backend. |
| **Caching** | `utils/redis_client.py` | Transparent in-process dict fallback when `REDIS_URL` unset. Used by fare cache, rate limiter, OTP lockout. |
| **Pub/sub** | `utils/ws_pubsub.py` | Single channel `spinr:ws:dispatch`. Producer publishes `{client_id, message}`; every replica's consumer delivers locally iff that client is on this replica. |
| **Background** | `core/lifespan.py` | 7 loops spawned at startup, cancelled at shutdown. Each loop reads DB state, so replicas competing is safe in the idempotent cases; dispatch uses atomic claim. |
| **Config** | `core/config.py` | `Settings` (pydantic-settings). Fail-fast in prod on weak defaults (`JWT_SECRET`, admin creds, Redis URLs). |

---

## 7. Data model — read-first paths

Full schema is authored in `backend/migrations/*.sql`. High-level groupings:

- **Identity:** `users`, `admin_staff`, `refresh_tokens`, `otp_records`.
- **Mobility:** `drivers`, `rides`, `ride_messages`, `driver_location_history`, `driver_daily_stats`, `driver_activity_log`, `driver_notes`.
- **Pricing:** `service_areas`, `vehicle_types`, `fare_configs`, `promotions`, `promotion_redemptions`, `area_fees`.
- **Money:** `wallets` (rider), `wallet_ledger`, `stripe_events` (idempotency), `payments`, `payouts`, `driver_bank_accounts`.
- **Growth:** `loyalty_points`, `loyalty_redemptions`, `quests`, `quest_progress`, `subscriptions`, `subscription_plans`.
- **Content/ops:** `saved_addresses`, `favorites`, `notifications`, `notification_preferences`, `support_tickets`, `faqs`, `flags`, `complaints`, `disputes`, `lost_and_found`, `audit_logs`.
- **Corporate B2B:** `corporate_accounts`, `corporate_members`, `corporate_wallets`, `corporate_wallet_transactions`, `corporate_kyb_documents`, `corporate_kyb_decisions`, `corporate_invites`, `corporate_policies`, `corporate_low_balance_notifications` (see `docs/CORPORATE_B2B.md`).

RLS policies live in `migrations/26_rls_coverage_gap.sql` and per-feature migrations. The backend runs as service role and bypasses RLS; mobile clients go through Supabase anon key (future) or the backend.

---

## 8. Deployment notes

- **Host:** Railway (prior Fly.io setup was scrubbed 2026-04-16). CLI commands use `railway`; CI needs `RAILWAY_TOKEN` and `RAILWAY_PUBLIC_URL` secrets.
- **Env:** see `core/config.py` for the full inventory. In prod, startup fails fast on weak `JWT_SECRET`, weak admin creds, missing `RATE_LIMIT_REDIS_URL` / `WS_REDIS_URL`, or placeholder `SUPABASE_*`.
- **Migrations:** `backend/migrations/` — numbered, append-only. Running: `supabase db push` or the project-specific script. `00_schema_migrations_table.sql` tracks applied migrations; `24_schema_migrations.sql` added the version column used by later migrations.
- **Secrets:** never commit. `SUPABASE_SERVICE_ROLE_KEY`, `JWT_SECRET`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `TWILIO_*`, `FIREBASE_SERVICE_ACCOUNT_JSON` all live in Railway variables.

---

## 9. Diagrams — where they live

- **System topology / boot / request lifecycle** — this file, §§ 2–4.
- **Dispatch state machine + dispatch flow** — `RIDES_AND_DISPATCH.md` §§ 3–4.
- **WebSocket fan-out** — `RIDES_AND_DISPATCH.md` § 10.
- **Auth flows (OTP, refresh, admin login, session revoke)** — `AUTH_AND_USERS.md` § 4.
- **Wallet top-up + Stripe webhook + autotopup** — `WALLET_AND_PAYMENTS.md` § 5, plus `docs/CORPORATE_B2B.md` for the B2B variant.
- **Corporate B2B full system map** — `docs/CORPORATE_B2B.md` (detailed; owns that subsystem end-to-end).

---

## 10. Conventions worth knowing

- **Money** — pass through `_d() → _round() → _f()` helpers before writing/responding. Prevents IEEE-754 drift.
- **Ride status transitions** — always guarded (`_require_ride_in_state`). Never update `status` without checking the `allowed_states` tuple for that action.
- **Stripe webhook idempotency** — `stripe_events` table. Claim with `claim_stripe_event(event_id)` before processing; `mark_stripe_event_processed` on success.
- **JWT role trust** — admin JWTs carry `role+email+modules` and are trusted. Rider/driver JWTs are not trusted for role; role comes from `users` table every request.
- **OTP** — 4 digits (product decision). Mitigated by rate limiting + short expiry + 24 h lockout after 5 failures.
- **Imports** — routes use `try: from .. import X; except ImportError: import X` so they work both as `python -m backend.server` and as a top-level package. Don't simplify away.
