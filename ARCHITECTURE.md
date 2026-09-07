# Spinr — Architecture

Canonical architecture reference for Spinr, a Canadian ride-sharing platform
(Saskatchewan-first) built on a **0% commission model** — drivers keep 100%
of fares and pay a flat subscription fee (Spinr Pass). This file merges what
previously lived in `docs/backend/ARCHITECTURE.md` and
`docs/framework/03-architecture-platform.md`; both now point back here.
Conventions and invariants that must hold day-to-day (money math, state
machine, insurance periods, RLS, etc.) live in `CLAUDE.md` and are only
cross-referenced below, not restated. Decisions and their rationale live in
`docs/adr/`.

---

## 1. System Topology

```
Rider App ──┐
Driver App ─┤── REST + WebSocket ──► FastAPI (Fly.io primary / Railway standby)
Admin ───────┘                            │
                             Supabase(Postgres+RLS)  Redis  Stripe
                             Firebase  Twilio  FCM  Cloudinary
```

- **Clients:** Rider App and Driver App (Expo/React Native, iOS + Android),
  Admin Dashboard (Next.js, web). All three talk to the backend over
  HTTPS/WSS using the same REST + WebSocket contract.
- **Backend:** one horizontally-scalable FastAPI process (Python), deployed
  to **both** Fly.io (Toronto, intended primary) and Railway (Canada, warm
  standby) in parallel, routed via a Cloudflare CNAME with DNS-level
  fail-over — see `CLAUDE.md` → Deployment and
  `docs/adr/007-fly-primary-railway-standby.md` for why, and
  `docs/runbooks/railway-fly-failover.md` for the fail-over procedure.
  Current standby status (Railway auto-deploy has been blocked) is tracked
  in `ACTION_ITEMS.md` C5 — check there before assuming standby is live.
- **Durable state:** Supabase (Postgres + RLS), in a Canadian region — moving
  it is a compliance event, not a config change.
- **Ephemeral state:** Redis — rate limiting, OTP lockout, fare cache,
  WebSocket fan-out across replicas via the `spinr:ws:dispatch` pub/sub
  channel. Falls back to an in-process dict when unset (dev only; required
  in production).
- **External services:** Stripe (rider payments + driver Connect payouts),
  Firebase (auth token verification, FCM push, Crashlytics, App Check),
  Twilio (SMS/OTP, console fallback in dev), Google Maps Platform (maps,
  places, directions, geocoding), Cloudinary (user image uploads).

## 2. Tech Stack

| Layer | Technology |
|-------|-----------|
| Rider App | React Native (Expo SDK 54), Expo Router, Zustand |
| Driver App | React Native (Expo SDK 54), Expo Router, Zustand |
| Admin Dashboard | Next.js 16 + TypeScript + Tailwind + shadcn/ui |
| Shared package (`@spinr/shared`) | TypeScript — API client, Zustand stores, shared types/components |
| Backend | FastAPI (Python 3.12), Pydantic v2, Uvicorn (ASGI) |
| Database | Supabase (Postgres), accessed via `supabase-py` service-role client |
| Auth | Firebase ID token + short-lived JWT (HS256) + rotating refresh tokens |
| Cache / pub-sub | Redis |
| Background jobs | `asyncio.create_task` loops spawned from `core/lifespan.py` (see `CLAUDE.md` for the current registry — 41 as of this writing) |
| Payments | Stripe (PaymentIntents, SetupIntents, Connect) |
| SMS | Twilio |
| Push / crash / integrity | Firebase Cloud Messaging, Crashlytics, App Check |
| File storage | Supabase Storage (`driver-documents`), Cloudinary |
| Observability | Loguru (JSON to stderr) + Sentry (10% traces/profiles) |
| Hosting | Fly.io (primary) + Railway (standby) for backend; Vercel for admin; EAS Build for mobile |

## 3. Backend Architecture

Layering: **routers → services → repositories.** `backend/routes/` holds
~25 domain routers (one file/folder per domain — rides, drivers, auth,
payments, wallet, corporate, fares, notifications, websocket, admin/...);
`backend/services/` is a thin service layer (dispatch, fare, corporate
wallet/membership/allowance); `backend/repositories/` (via `db_supabase.py`,
~66 helpers) owns DB access and query-filter escaping — see CLAUDE.md's
query-filter rules before touching any `$regex`/`$or` filter.

### Request lifecycle

```
HTTP request
  → CORSMiddleware              (ALLOWED_ORIGINS env; "*" rejected in prod)
  → SecurityHeadersMiddleware   (X-Frame-Options, HSTS prod-only, CSP)
  → RelativeRedirectMiddleware
  → SlowAPI RateLimitMiddleware (Redis-backed; per-route overrides)
  → FastAPI router              (server.py mounts ~25 routers)
  → Dependencies                (get_current_user, get_admin_user)
  → Route handler                (Pydantic validates → services/repositories)
  → Exception handlers          (SpinrException → schema; 422; X-Request-ID)
```

WebSocket (`routes/websocket.py`): client connects to
`/ws/{client_type}/{client_id}`, first message must be
`{"type": "auth", "token": "<jwt>"}`, connection is registered as
`"driver_{id}"` / `"rider_{id}"`, a heartbeat runs (10 s interval per
`CLAUDE.md` — tightened from 30 s), and messages are rate-limited
(30 msg/s, 64 KB max). Outbound server → client fan-out goes through Redis
pub/sub so a driver on one replica can message a rider on another; every
replica's subscriber delivers locally only if the target client is
connected there. Full message catalog: `docs/backend/RIDES_AND_DISPATCH.md`.

### Domain map

For "where do I find X", the per-domain deep-dives under `docs/backend/`
are the reference, not this file:

| Domain | Doc |
|---|---|
| Auth, users, staff | `docs/backend/AUTH_AND_USERS.md` |
| Rides, dispatch, drivers, fares | `docs/backend/RIDES_AND_DISPATCH.md` |
| Wallet, payments, loyalty, promotions | `docs/backend/WALLET_AND_PAYMENTS.md` |
| Corporate B2B | `docs/CORPORATE_B2B.md` |
| Infrastructure / platform (server, lifespan, middleware, config) | `docs/backend/INFRASTRUCTURE.md` |
| Admin / ops | `docs/backend/ADMIN_AND_OPS.md` |
| Function/class index | `docs/backend/REFERENCE.md` |

### Cross-cutting concerns

CORS, security headers, auth, rate limiting, OTP brute-force protection,
error handling, logging, DB access, caching, pub/sub, background-loop
replay safety, and config-in-DB are all handled centrally — see
`docs/backend/INFRASTRUCTURE.md` for the implementation map and `CLAUDE.md`
→ Critical Conventions for the rules that must not be violated (dual-import
pattern, Decimal-only money math, `_require_ride_in_state()` guards, JWT
trust model, Stripe idempotency, insurance-period logging).

## 4. Frontend Surfaces

| Surface | Framework | Notes |
|---|---|---|
| Rider App (`rider-app/`) | Expo/React Native, Expo Router, Zustand, Stripe React Native | Booking, live tracking, payments, SOS |
| Driver App (`driver-app/`) | Expo/React Native, Expo Router, Zustand | Dashboard, earnings, Stripe Connect payouts, SOS |
| Admin Dashboard (`admin-dashboard/`) | Next.js 16, TypeScript, Vercel-hosted | Service-area config hub, ride/driver detail, staff, audit logs |
| Shared (`shared/`, `@spinr/shared`) | TypeScript | API client, Zustand stores, shared components (e.g. `SOSButton`) — consumed by all three above |

All three client surfaces consume the same REST/WS contract; shared logic
belongs in `shared/`, and a change to a shared component must name every
importer in its blast radius before merge (see `CLAUDE.md` pre-merge gates).
See root-level directory listing in this repo, or each surface's own
manifest, for its full file layout.

## 5. Payments Architecture

At booking, the backend places a **manual-capture** Stripe PaymentIntent
(a hold) on the rider's card; money is captured only at ride completion via
`POST /rides/{id}/process-payment`, which is guarded by a DB-level
optimistic-lock claim and dispatches to one of three settlement paths —
wallet (atomic RPC), corporate allowance (`corporate_wallet_apply_delta`),
or card (Stripe capture/charge). Every mutating Stripe call carries an
idempotency key; webhooks are deduplicated via a `stripe_events` claim
table (`claim_stripe_event` / `unclaim_stripe_event` on failure so Stripe's
retry isn't lost); all money movement is recorded to the append-only
`financial_events` ledger (7-year retention).

This is a summary only — the full request/response flows, sequence and
flowchart diagrams, 3DS/SCA behavior, webhook event catalog, background
reconciliation loops, and the currently-known gaps (e.g. the rider app's
unreachable in-app 3DS challenge path) are documented in the dedicated
deep-dive:

**→ `docs/architecture/payments-rider-stripe.md`**

For the invariants that must never be violated (Decimal-only arithmetic,
`claim_stripe_event` before processing any webhook), see `CLAUDE.md` →
Critical Conventions.

## 6. Business Model

```
Revenue Sources:
  1. Spinr Pass (driver subscriptions) — Basic/Pro tiers, configured per service area
  2. Cancellation fees — split between driver and platform
  3. Platform fees (per ride) — configurable per service area

Driver Earnings:
  100% of ride fare (0% commission) + 100% of tips + cancellation fee share

Service Area = primary configuration unit:
  vehicle pricing, fees & taxes, cancellation fees, Spinr Pass plans, required driver documents
```

See "What Spinr Is NOT" in `CLAUDE.md` for the guardrails this model
implies (never a commission-taking marketplace, never unbounded surge,
never a hidden-fee operator).

## 7. Security

| Layer | Implementation |
|-------|---------------|
| API auth | JWT (Bearer); admin JWTs trusted (role+email+modules in claims), rider/driver role always re-read from `users` table |
| OTP | Twilio SMS (production), `1234` dev bypass when `ENV != production`; SHA-256 hashed at rest, 24 h lockout after 5 failures/hour |
| Payments | Stripe (PCI-DSS compliant, no card data stored) |
| App integrity | Firebase App Check |
| Transport | HTTPS enforced; CORS configured per environment |
| Rate limiting | Per-endpoint, Redis-backed (SlowAPI) |
| Audit trail | Admin action logging |
| RLS | Second, independent enforcement layer in Supabase under the API |

Full rules (JWT trust model, OTP lockout mechanics, RLS coverage) live in
`CLAUDE.md` → Critical Conventions; RLS policy tests live in
`backend/tests/rls/`.

## 8. Architectural Principles

These hold the system coherent as it grows; full detail and rationale live
in `docs/framework/03-architecture-platform.md` (Pillar 3):

1. **State machines over statuses** — `rides.status` is an enumerated,
   guarded transition graph, not a free-form field; any value outside it is
   a contract violation.
2. **Money is Decimal, once-written, idempotent** — one way to move money in
   each direction; new code calls it, never reimplements it.
3. **Layered trust, re-verified at the boundary** — admin JWTs are trusted;
   rider/driver role is re-read from the DB every request; RLS is a second,
   independent layer.
4. **Derived truth over duplicated truth** — e.g. `is_available` is computed
   from `is_online` + ride/offer state, never set independently; insurance
   periods derive from ride state, not driver UI.
5. **Degrade loudly, never silently** — Redis/DB/payment/dispatch failures
   surface as errors with documented consequences, never a silent fallback
   that masks the symptom.
6. **Config over deploys** — rotatable secrets and operational toggles live
   in the `app_settings` table, not `.env`.
7. **Additive schema evolution** — migrations are append-only,
   filename-keyed; prefer a new column/flag over repurposing one.
8. **The corporate layer is a lamination, not a fork** — corporate billing
   attaches at the settlement/notification/reporting seams of the consumer
   product; it does not fork the core ride lifecycle.

Deliberately **not** built (yet): microservices, Kubernetes, event-sourcing,
multi-region active-active, service meshes — see Pillar 3 §"What we
deliberately do not build" and Pillar 7 (scorecard) for the triggers that
would revisit this.

## 9. Where Decisions Live

Structural decisions — deployment topology, trust-model changes, new
external dependencies, new background loops — are recorded as ADRs, not
restated here:

**→ `docs/adr/`** (see `docs/adr/README.md` for the index; e.g.
`001-supabase-postgres.md`, `002-expo-react-native.md`,
`003-fastapi-backend.md`, `005-jwt-firebase-dual-auth.md`,
`006-railway-deployment.md`, `007-fly-primary-railway-standby.md`)

Sprint-scoped context, domain deep-dives (dispatch, payments, corporate,
safety), and regulatory obligations are loaded on demand per `CLAUDE.md`'s
Context Imports section rather than duplicated here.

---

*Last reorganized 2026-09-07, consolidating `docs/backend/ARCHITECTURE.md`
and `docs/framework/03-architecture-platform.md` into this file per the
same pattern used for `docs/PRD.md`. Update this file whenever system
architecture changes; update the deep-dives it links to for domain detail.*
