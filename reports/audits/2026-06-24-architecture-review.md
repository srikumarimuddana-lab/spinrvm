# Spinr — Comprehensive Architecture & Code Review

**Date:** 2026-06-24
**Reviewer:** Engineering Director / Chief Architect review (read-only)
**Scope:** Backend (FastAPI), rider-app, driver-app, admin-dashboard, shared, infra/CI
**Method:** Five parallel grounded reviews (core ride/money, security/PII, error/telemetry, frontend, infra/stack) + direct verification of the highest-impact findings.
**Benchmark:** Compared against rideshare market leaders (Uber/Lyft) where relevant.

> This is an analytical teardown and a prioritized plan. No application code was modified.

---

## TL;DR — Manager's Verdict

The platform is **substantially more mature than a typical Saskatchewan-first startup** and, in several places, more disciplined than early-stage Uber/Lyft code: hash-pinned dependencies with `--require-hashes`, image signing (cosign), Trivy/Bandit/Semgrep/pip-audit gates, PostGIS geo-matching, self-hosted OSRM with Redis ETA caching, replay-safe background loops, a typed shared API client with refresh-storm dedup, memory-only access tokens with SecureStore/HttpOnly refresh, and a 4-handler global exception stack that sanitizes 5xx detail. The prior P0 security sprint genuinely closed its findings.

The remaining risk is concentrated in **four pockets**, not spread evenly:

1. **One verified launch-blocking config bug** — production Firebase App Check will 401 every Stripe/SES webhook.
2. **Money-path atomicity** — corporate settlement saga and the cancellation-fee debit are non-atomic read-modify-writes, unlike every other money mutation (which correctly use Postgres RPCs).
3. **Runtime architecture** — synchronous PostGREST DB access behind a 64-thread pool (no real Postgres connection pooler), and 16 background loops co-resident with the web/WS request path on a 2-worker/1GB VM. Correct today; a scaling ceiling tomorrow.
4. **Observability depth** — strong metrics + Sentry, but no distributed tracing, no load testing in CI, no feature flags, and several documented KPI metrics not actually wired.

**Overall health: B+ / "ship-ready after the P0 list, with a clear P1 hardening runway."** The codebase is maintainable and the conventions in `CLAUDE.md` are real and largely enforced. The biggest structural debt is the 5,811-line `routes/rides.py` and the half-finished service extraction.

---

## 🚨 Critical Issues & Security Flaws

### C1 — [P0, VERIFIED] Production App Check middleware blocks every Stripe & SES webhook
- **Where:** `backend/core/middleware.py:70-88` (`_APP_CHECK_EXEMPT_PREFIXES`) vs. real route `backend/routes/webhooks.py:27,344` → mounted path `/api/v1/webhooks/stripe` (`server.py:319,334`). Middleware added with `enforcement_enabled=is_production` (`middleware.py:583`).
- **What:** The exempt list has no `/api/v1/webhooks/` entry. In production the middleware checks `X-Firebase-AppCheck` on every `/api/*` path; Stripe and AWS SES cannot send that header, so the request returns **401 "App Check token required" before the handler runs**. Separately, the CSRF exempt entry at `middleware.py:31` points at the stale path `/api/v1/stripe/webhook` (real path is `/api/v1/webhooks/stripe`) — latent because CSRF only fires on Origin-bearing requests.
- **Why it matters:** The moment `ENV=production`, payment confirmation, refunds, disputes, payout reconciliation, subscription renewals, and SES bounce/complaint handling all silently fail. Pre-launch today, so it is a **launch-gating** bug, not an active outage — but it would not surface in staging (enforcement off there).
- **How:** Add `/api/v1/webhooks/` to `_APP_CHECK_EXEMPT_PREFIXES`; fix the stale CSRF entry to `/api/v1/webhooks/stripe`. Add a test that asserts the *live* route path (resolved from the router, not a hardcoded string) is exempt so the allowlist can't drift from the mount point again.

### C2 — [P0] Corporate settlement saga is non-atomic with inverted-sounding primitives
- **Where:** `backend/services/payment_service.py:279-330` (`settle_corporate`), allowance ops in `corporate_allowance_service.py:58-121`.
- **What:** Allowance debit and master-wallet debit are two separate awaited writes with hand-rolled compensation only in the `except`. A process crash/timeout *between* the two writes leaves the allowance consumed, the master wallet un-debited, and the ride stuck `processing` — with **no idempotency key and no single-transaction guarantee** (unlike `settle_wallet`'s single `wallet_pay_for_ride` RPC). Compounding it, settlement *consumes* allowance via `apply_rollback` and *refunds* via `apply_grant` — semantically inverted from the function names and docstrings; needs verification against the sign convention in `_apply` (if signs don't match intent, it is a live money bug, not just a naming trap).
- **Why it matters:** Corporate billing correctness; silent partial settlements that only offline reconciliation can catch.
- **How:** Move allowance + master debit into one `SECURITY DEFINER` Postgres function keyed/idempotent on `ride_id` (mirror `wallet_pay_for_ride` / `corporate_wallet_apply_delta`). Rename the allowance primitives to match their effect.

### C3 — [P0] Cancellation-fee wallet debit is a non-atomic read-modify-write
- **Where:** `backend/routes/rides.py:4462-4490` (`cancel_ride_rider`).
- **What:** Reads `balance`, computes `new_balance` in Python, writes the literal value — no `FOR UPDATE`, no conditional filter on prior balance, and writes via `_f(new_balance)` (float at the boundary). Every other money mutation uses an atomic RPC.
- **Why it matters:** Concurrent writers (cancel racing a top-up, or a double-tapped cancel that slips past the status claim) lose a write and mischarge the rider.
- **How:** Route the cancellation fee through an atomic wallet-debit RPC with a `reference_id`/idempotency guard.

### C4 — [P1] Auth-lockout Redis state can silently fall to per-process dict in production
- **Where:** `core/lifespan.py:116-123` (logs ERROR, does not raise), `middleware.py:470-481` (`_validate_production_config` hard-requires only `RATE_LIMIT_REDIS_URL`), `utils/redis_client.py:80` (reads `REDIS_URL`).
- **What:** OTP lockout, admin login/TOTP lockout, break-glass counters, and session-revocation fast-path use `redis_client` (`REDIS_URL`), a *different* variable than the rate limiter's `RATE_LIMIT_REDIS_URL`. A prod deploy can pass the startup gate with `RATE_LIMIT_REDIS_URL` set while `REDIS_URL` is unset — pushing security lockouts into a per-process dict that resets on restart and isn't shared across replicas.
- **How:** Require `REDIS_URL` in `_validate_production_config` (or point the auth-lockout helpers at the validated variable).

### C5 — [P1] Stripe wallet/corporate top-up branches credit before marking processed
- **Where:** `backend/routes/webhooks.py:422` (claim), `:463` (corporate), `:483` (wallet).
- **What:** `claim_stripe_event` runs first (good), but the wallet/corporate branches perform the money mutation, *then* `mark_stripe_event_processed`. If `claim_stripe_event`'s `is_new` reflects `processed_at IS NULL` rather than pure row existence, a crash between increment and mark lets a Stripe retry re-credit. Verify the claim semantics; the ride branch is safe (idempotent status set), the raw wallet/corporate `increment` is the risk.
- **How:** Make the wallet/corporate increments idempotent on `stripe_payment_intent_id` rather than relying on claim ordering.

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**Strengths (verified, do not re-flag):** `utils/error_handling.py` maps unhandled exceptions to a clean 500 JSON with no stack trace, sanitizes 5xx `HTTPException` detail unless it's an `ERR_*` sentinel, binds `request_id` end-to-end, and uses `logger.opt(raw=True)` so the handler can't itself crash on `{`-containing tracebacks. Sentry is wired via a PII-scrubbing loguru sink. Mobile has Sentry + Crashlytics + an `ErrorBoundary` with PII-stripping `beforeSend`. No raw phone/email/lat-lng/name/license found in logs across surfaces.

### E1 — [P0] `notify_safety_team` undefined on the non-package import path
- **Where:** `backend/utils/safety_checkin_loop.py:36-43`; used at `:191` and `_ws_manager` at `:38`.
- **What:** The `except ImportError` fallback (`:41-43`) imports `send_push_notification` but **not** `notify_safety_team` or `_ws_manager`. Under the standard `python3 -m backend.server` entrypoint (which hits the fallback), escalation raises `NameError`. *Verified nuance:* the incident row is still inserted (`:180`), `_escalated_key` is set before the notify call (`:183`), and an error is logged (`:193`) — so it does **not** retry forever and is not fully silent, but the safety-team WS broadcast + email paging **never fires** and logs a generic failure each time.
- **Why it matters:** A non-responding rider's safety escalation is recorded but the on-call team is never paged.
- **How:** Add `notify_safety_team` (and `_ws_manager`) to the `except ImportError` import line.

### E2 — [P1] Two competing global `Exception` handlers; the weaker one leaks `str(exc)`
- **Where:** `core/middleware.py:586-624` (`cors_exception_handler`) vs. `utils/error_handling.py:888` (`general_exception_handler`, registered at `server.py:293`).
- **What:** Two handlers for the same exception type — order-dependent and fragile. The middleware one (`:590-591`) returns `{"detail": exc.detail}` for any object with `.status_code`/`.detail` with **no 5xx sanitization**, bypassing the `ERR_*` sanitizer if it wins.
- **How:** Register the `Exception`/`HTTPException` handler in exactly one place (keep `error_handling.py`); drop or narrow the catch-all in `middleware.py`.

### E3 — [P1] Warning-and-continue on critical paths (violates `CLAUDE.md`)
- Dispatch presence-filter failure → `logger.warning` and fail-open to all DB-`is_online` drivers (`dispatch_service.py:301-310`). Keep the metric, but log ERROR with `exc_info` so it pages.
- `init_database` health-check failure outside production → ERROR then `logger.warning("Continuing…")` and boots against a dead DB (`lifespan.py:60-64`, also `:29`). Fail fast / fail readiness probe.
- Safety check-in import failures logged at WARNING, not ERROR (`lifespan.py:302,325,336`) — below the Sentry sink threshold, so a safety/reconciliation loop that fails to import silently never starts.
- Cancel-attribution write downgrades any DB error to WARNING and retries with a narrower payload assuming PGRST204 (`rides.py:4532-4534`).

### E4 — [P1] Safety check-in send/escalate are read-then-write, not replay-safe
- **Where:** `utils/safety_checkin_loop.py:103-127,135-183`.
- **What:** Read `_sent_key` → send push → set key. All 16 loops run on every replica; two replicas reading a missing key before either writes both send the "are you OK?" push.
- **How:** Use `redis_set_nx` to *claim* the key (the pattern `payment_retry` already uses) and only act on a won claim.

### E5 — [P1] Watchdog misses safety + financial loops, and names don't match
- **Where:** `core/lifespan.py:393-411` (`_WATCHDOG_LOOP_NAMES`); heartbeat recorded as `"safety_checkin_loop"` (`safety_checkin_loop.py:70`) vs. spawned task name `"safety_checkin (30s)"`.
- **What:** Watchdog omits `safety_checkin`, `reconciliation`, `driver_claim_reaper`, `preauth_capture`, `referral_payout`, `surge_engine`. A wedged safety/reconciliation loop produces no Slack alert; even if added, the name mismatch reads as permanently stale.
- **How:** Align heartbeat names to task names; add the safety/financial loops to the watchdog list.

### E6 — [P1] Frontend: out-of-order WS events move ride UI backwards
- **Where:** `rider-app/store/rideStore.ts:1034-1061`, `rider-app/hooks/useRiderSocket.ts:174-179,282`.
- **What:** The store applies any incoming status verbatim with no forward-transition guard; the envelope carries a `seq` that is unwrapped and discarded. A late/duplicate `driver_arrived` after `ride_started` visibly regresses the UI — a ride-state-machine contract violation.
- **How:** Validate the transition against the allowed state map before applying; drop frames with a `seq` older than last-seen per ride.

### E7 — [P2] Telemetry gaps vs. documented KPIs
- `utils/payment_retry.py` emits **no** metrics despite `spinr_payment_settlement_total{outcome=...}` being a source-of-truth metric. Dispatch emits only `presence_filter_failed_total`; the documented `spinr_dispatch_offer_sent_total` / `offer_accepted_total` / `offer_to_accept_duration_ms` were not found wired — KPI dashboards (match rate, P95 dispatch latency) have no data source.
- `utils/metrics.py:38-61` has no label-cardinality cap — a caller that puts `ride_id`/`driver_id` in a label grows the registry unbounded (slow leak + Prometheus cardinality blowup).
- Mobile/admin: unconditional `console.log` of WS URLs (`useRiderSocket.ts:257,262,303`) reaches LogRocket session recordings; gate behind `__DEV__` to match the client convention.

---

## 🐢 Performance Bottlenecks & Optimizations

### P-1 — [P1] Inline blocking Stripe calls on the booking-critical path
- **Where:** `backend/routes/payments.py:117-141` and throughout (`Customer.create`, `PaymentIntent.create`, `PaymentMethod.list`, `EphemeralKey.create`).
- **What:** The synchronous Stripe SDK is awaited inside async handlers with no thread offload — blocks the event loop for the full Stripe round-trip, throttling every coroutine on the replica. `CLAUDE.md` explicitly names this anti-pattern.
- **How:** `asyncio.to_thread` / `run_in_threadpool` the sync Stripe calls, or adopt Stripe's async client.

### P-2 — [P1] Surge counts do full-table reads with a 5,000-row Python cap
- **Where:** `backend/utils/surge_engine.py:80-201` (default spatial flag off, `:49`).
- **What:** Per area, every 2 minutes, pulls up to 5k driver rows and 5k ride rows and point-in-polygon filters in Python. The code itself logs an error + metric when the cap truncates — a **regulated-price correctness risk**, not just perf. The PostGIS fix (`migration 170`, `SURGE_SPATIAL_COUNT`) exists but ships disabled. `get_surge_status` (admin-facing) recomputes this live, uncached, per call.
- **How:** Rehearse and enable the spatial count; cache and share the computation between the loop and the status endpoint.

### P-3 — [P1] Offer-timeout / re-dispatch is in-process `asyncio.create_task`, not replica-durable
- **Where:** `backend/routes/rides.py:1241-1412`.
- **What:** Authoritative 30s offer expiry sleeps in-process. A redeploy/OOM during the window loses the timer: driver stays claimed (`is_available=False`), ride stuck until the slow stuck-ride sweeper notices. The docstring overstates crash-survival. With frequent Fly/Railway deploys this is a real abandonment vector.
- **How:** Back the offer TTL with a DB column (`offer_expires_at`) reaped by an existing loop.

### P-4 — [P1] Admin live map tears down and rebuilds every marker per location update
- **Where:** `admin-dashboard/src/components/driver-map.tsx:143-204`.
- **What:** Effect on `[drivers, serviceAreas]` removes all markers and rebuilds them, and re-runs `fitBoundsToPoints` on every WS `driver_location_update` — full DOM marker churn several times/second on a busy fleet, and the map constantly re-pans so operators can't navigate.
- **How:** Diff by driver id (update `setLngLat` on existing markers); gate auto-fit to first load / explicit recenter.

### P-5 — [P2] No fare-estimate caching (ETA/route are cached, fare is not)
- **Where:** `utils/maps_eta.py` (15s TTL) and `route_distance.py` (OSRM) cache; `services/fare_service.py` shows no cache. Fare estimate SLA is <300ms.
- **How:** Cache estimates keyed by `(geohash_pickup, geohash_dropoff, vehicle_type, surge_epoch)` with a 30–60s TTL pinned to the 2-minute surge recalculation epoch.

### P-6 — [P2] N+1 in driver-release / offer-loser loops
- **Where:** `rides.py:1241/1415`, `drivers.py:3642-3654` — `get_driver_by_id` per driver in a loop. Bounded by `max_offers ≤ 10` so tolerable, but duplicated 3–4 times so the insurance/period logic must be fixed in every copy.

---

## 💡 Tech Stack & Architecture Recommendations

### T1 — [P0-arch] Replace synchronous PostgREST access on hot paths with asyncpg + a real pooler
- **Where:** `backend/repositories/_base.py:136-201`, `db_supabase.py`.
- **What:** Every DB call is `supabase-py` (sync, REST/PostgREST over HTTP/2) dispatched to a 64-thread executor. There is **no PgBouncer / transaction pooler** — the guarded "connection pool" is an HTTP pool. Concurrency is bounded by 64 threads × 2 workers; saturation manifests as latency, not backpressure; N+1s pay the HTTP tax per row. This is the single biggest liability against the P95<2s dispatch SLA.
- **How:** Introduce **asyncpg + SQLAlchemy 2.0 async** (or asyncpg directly) for dispatch/fare/location hot paths, pointed at Supabase's **transaction-mode pooler (port 6543)**. Keep supabase-py for admin/low-traffic CRUD. Instrument first (`spinr_db_calls_total` latency by query) — if `match_and_claim_driver` RPC P95 is already <50ms you may only need asyncpg for location writes and dispatch.

### T2 — [P0-arch] Extract the 16 in-process background loops to a worker process
- **Where:** `backend/core/lifespan.py:135-434`, `fly.toml:11-26` (2 workers / 1GB).
- **What:** Surge engine, payment retry, reconciliation, T4A, retention purge, sweepers all run inside the uvicorn process serving rider/driver HTTP+WS. Replay-safe (so not a correctness bug) but a resource-isolation problem: a reconciliation burst competes with the <2s dispatch path, and you cannot scale web capacity without multiplying every loop's DB load.
- **How:** **arq** (asyncio-native, Redis-backed) or a dedicated Fly process group running the same image with a `--worker` entrypoint and `min_machines_running=1`. Low-risk lift since loops are already idempotent. Do **not** reach for Kafka/Celery-beat yet.

### T3 — [P1] Add distributed tracing (OpenTelemetry)
- You have Prometheus metrics + Sentry + structured loguru but no request-level tracing. For rider tap → fare → Maps/OSRM → dispatch RPC → WS fan-out across replicas, a P95 breach isn't attributable to a hop. Add **OpenTelemetry** (FastAPI + httpx + asyncpg auto-instrumentation), OTLP → **Grafana Tempo or Honeycomb**, propagate `ride_id` as a span attribute. Highest-leverage observability add.

### T4 — [P1] Load testing in CI + gate the committed perf baselines
- `backend/tests/perf_baseline.py` and `perf_*_before.json` exist but no workflow runs them, and there is no concurrency load test hitting the 64-thread ceiling (exactly where T1's risk lives). Add a **k6** scenario for dispatch + fare hot paths nightly against staging; wire `perf_baseline.py` as a regression gate.

### T5 — [P1] Feature flags / progressive delivery
- Risky changes (surge tweaks, dispatch radius) ship behind global `app_settings` booleans with no percentage rollout, per-region targeting, or fast kill-switch. Adopt **OpenFeature** backed by self-hosted **Unleash** (Canadian-data-residency friendly, avoids US-SaaS PIPEDA review) to canary dispatch changes by service area.

### T6 — [P1] Deploy coordination + canary
- `deploy-fly.yml` and `deploy-backend.yml` deploy Fly and Railway **independently in parallel from main** with no coordination; no canary stage; `max_unavailable=1` on 2 machines churns 50% capacity; the post-deploy smoke test (`ci.yml:610`) runs *after* full rollout (detects but doesn't prevent). Adopt Fly canary (1 machine soak), and gate the Railway/Fly fan-out on the canary's smoke test — cheap given the DNS-swap failover model.

### T7 — [P2] API contract testing
- FastAPI emits OpenAPI but nothing validates the mobile apps' expectations against the served schema. Add **schemathesis** (property-based from the live spec) in CI — catches the 401-vs-500 class the smoke test currently checks by hand.

### T8 — [P2] WS-Redis as a production boot-blocker
- `lifespan.py:485-497`: with >1 replica and WS pub/sub unstarted, driver and rider land on different machines and dispatch events silently vanish — yet prod boots on a WARNING. With `min_machines_running=2`, single-machine coherence is never the prod reality. Make missing WS Redis in production a boot-blocker / readiness=false + Sentry alert, consistent with the "don't swallow dispatch errors" rule.

### What is correctly **absent** vs. Uber/Lyft (do not over-engineer)
- **Kafka** — correctly absent. At Saskatchewan volume, Redis pub/sub for fan-out + Postgres event log is right. If analytics demand grows: Supabase logical replication → warehouse, or Redis Streams — not a Kafka cluster.
- **H3 hex indexing** — correctly absent. You already use **PostGIS `geography(Point,4326)` + `ST_DWithin`** (`migrations/170`, `77_match_and_claim_driver.sql`); H3 only pays off at multi-market city-grid scale. *One check:* confirm a **GiST index on `location_geog`** ships in the same migration as the column (your own index-with-query-pattern rule) and that `match_and_claim_driver` uses it, not a seq scan.
- **Dedicated matching microservice (DISCO)** — correctly absent. The atomic `match_and_claim_driver` Postgres RPC is simpler and correct for the volume; the right evolution is T1 + T2, not a microservice.

---

## 🛠️ Maintainability & Code Smells

- **`routes/rides.py` is 5,811 lines.** `match_driver_to_ride` (~615 lines) and `create_ride` (~863 lines) are god functions. `DispatchService` holds the pure logic but the route still owns imperative orchestration — the extraction is half-done. Continue moving offer-insertion + notification into a `NotificationService` so the route is a thin composition.
- **Two near-identical offer-timeout handlers** (`rides.py:1241` legacy direct-assignment vs `:1415` batch) with subtly different release/period logic — confirm both are wired and batch rides don't depend on the dead path (`:1285` early-returns for batch).
- **Duplicated money/period sequences** in 3–4 places (`set_driver_available` + `record_period_transition` + WS-notify) — insurance-period fixes must be made everywhere.
- **Frontend duplication:** `CancelReasonSheet` and `BrandSplash` copied across rider/driver apps and drift independently (`CarMarker` is correctly shared, proving the pattern). Extract to `shared/components` with a presentation prop.
- **`shared/api/offlineQueue.ts`** (202 lines) is fully implemented but **never wired** — false resilience confidence, and replaying ride-lifecycle POSTs FIFO without idempotency keys would be unsafe. Delete it or wire it with a safe-to-replay allow-list + idempotency keys.
- **OTP/login screens bypass the typed error client** — `otp.tsx:125-148` hand-parses `err.response.data.detail` and regexes retry seconds out of a string, despite the client throwing typed `RateLimitError` with parsed `retryAfterSeconds`. Branch on `instanceof RateLimitError`.
- **Hardcoded placeholder UI values** on `ride-in-progress.tsx:66-67,341,396` (`'12:45 PM'`, `'4th Avenue North'`, `'1055 Canada Place'`, fixed 15-min progress) can leak into the safety-share message; `currentLocation` is never driven from GPS.

---

## 🧪 Testing & QA (missing edge cases)

- **Per-domain coverage floor not enforced.** `pytest.ini:15` global floor is 60%, but `CLAUDE.md` mandates ≥90% (payments/fare/crypto) and ≥80% (rides/dispatch). A payments regression below 90% passes CI. Add per-file thresholds (coverage.py per-file config or a CI assertion). *(This is ACTION_ITEMS A1, still open.)*
- **Missing edge-case tests to add:**
  - Corporate settlement crash *between* allowance and master debit (C2) — assert no orphaned allowance consumption.
  - Cancellation-fee concurrent debit race (C3).
  - Out-of-order WS frames (E6) — `driver_arrived` after `in_progress` must not regress UI.
  - Webhook idempotency: replay a wallet/corporate top-up event after a simulated crash before `mark_processed` (C5).
  - App Check exemption: live-route-path test for `/api/v1/webhooks/stripe` (C1).
  - Safety escalation under the non-package import path (E1) — assert `notify_safety_team` is reachable.
  - Surge count truncation at the 5k cap (P-2) — assert correct multiplier vs. spatial path.
- **Break-glass admin flow** (`admin/auth.py:1259-1275`) mints `user_id="break-glass"`, which the staff-lookup branch (`dependencies/__init__.py:232`) will reject (no `admin_staff` row) → the emergency path may be dead. Add an end-to-end test.
- **No API contract test** (T7) and **no load test** (T4) — the two highest-value test *types* missing.

---

## 📈 Manager's Verdict — Plan & Sequencing

**Health:** B+. Security-mature, conventions are real and enforced, money math is `Decimal`-correct at the core. The debt is concentrated and addressable.

### Do now — launch-gating (P0)
1. **C1** App Check webhook exemption (1-line fix + drift test) — *blocks all payment processing in prod.*
2. **C2** Corporate settlement → atomic idempotent Postgres function + rename `rollback`/`grant`.
3. **C3** Cancellation-fee debit → atomic RPC.
4. **E1** Add `notify_safety_team`/`_ws_manager` to the fallback import (1 line, safety path).

### Next — pre-launch hardening (P1)
5. **C4/C5** Require `REDIS_URL` in prod; make top-up increments idempotent on PI id.
6. **E2** Collapse to a single global exception handler.
7. **E3/E4/E5** Warning→error sweep on critical paths; `set_nx`-claim safety check-ins; fix watchdog coverage + names.
8. **P-1** Thread-offload inline Stripe calls. **P-3** DB-backed offer TTL. **P-2** Enable spatial surge counts.
9. **E6** WS forward-transition guard (rider) + **P-4** admin map marker diffing.
10. **T1/T2** Begin async-driver spike on the dispatch path + extract loops to an arq worker (instrument first).

### Then — depth & scale (P1→P2)
11. **T3** OpenTelemetry tracing. **T4** k6 load test + perf-baseline gate. **T5** feature flags (OpenFeature/Unleash). **T6** canary + cross-provider deploy coordination.
12. **T7** schemathesis contract tests. **T8** WS-Redis boot-blocker. **A1** per-domain coverage gates.
13. Maintainability: decompose `rides.py`, de-duplicate offer-timeout handlers and shared frontend components, delete/wire `offlineQueue.ts`.

**What not to do:** no Kafka, no H3, no matching microservice at this scale — the current PostGIS + Redis + single-process design is the right altitude. Invest in async DB + worker isolation + tracing instead.
