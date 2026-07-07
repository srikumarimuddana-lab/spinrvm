# Spinr — Comprehensive Architecture & Code-Health Teardown

**Date:** 2026-07-07
**Reviewers:** 5 parallel domain reviews (dispatch/realtime, payments/fare, auth/security, platform/SRE, client/testing)
**Mode:** Read-only. No code was modified. This is an analytical teardown + prioritized plan.
**Benchmark:** Uber / Lyft production-grade ride-share platforms.

---

## Executive Summary

Spinr is a **mature, disciplined codebase** — not a prototype. The money layer uses `Decimal` end-to-end, Stripe idempotency is enforced on every webhook, refresh-token rotation with reuse-cascade is implemented, admin routers are auth-gated by default, OTP rate-limiting fails *closed* on Redis loss, and there are **303 backend test files** including a ride-state-machine suite and Stripe-webhook version tests. Token storage on device is correct (access token in memory, refresh in hardware-backed SecureStore / HttpOnly cookie). The team has clearly worked through a P0 security sprint and closed it.

The residual risk is **not** in the "happy path" — it is concentrated in three seams where the system quietly degrades instead of failing loudly, contradicting the codebase's own stated rules:

1. **Edge/proxy trust** — per-IP rate limiting trusts a client-controlled header, voiding the SMS-OTP throttle (toll-fraud / account-takeover amplifier).
2. **Redis fail-open** — a transient Redis blip silently disarms leader locks, OTP lockout, and rate limiting, and makes background finance loops run on every replica.
3. **Money reversal accounting** — refunds/chargebacks mutate a status string with no offsetting ledger entry and no driver clawback in a 0%-commission model.

Against Uber/Lyft the biggest *architectural* gaps are: greedy per-ride matching (no batched/global assignment), no double-entry ledger, no distributed tracing / cross-replica metric aggregation, and an "offline-first" client layer that is actually dead code.

**Overall health: B+ / "production-capable, pre-scale."** Safe to launch in a single region at moderate volume once the CRITICAL and HIGH items below are addressed; the architectural items are what stand between Spinr and Uber/Lyft-grade reliability at scale.

---

## 🚨 Critical Issues & Security Flaws

### C1 — Per-IP rate limiting is bypassable via `X-Forwarded-For` spoofing *(CRITICAL)*
`utils/rate_limiter.py:101-105` — the default limiter keys on slowapi's `get_ipaddr`, which reads the **leftmost, fully client-controlled** `X-Forwarded-For` value with no trusted-proxy allowlist. Cloudflare/Fly *append* to XFF, so a forged first entry wins.
- **Why it matters:** every per-IP gate is void. `/auth/send-otp` (3/min) has **no per-phone backstop**, so an attacker can SMS-bomb any victim phone and run up unmetered Twilio cost (toll fraud). `/auth/verify-otp` (5/min) becomes 5-guesses-per-phone-per-day *per rotated IP*, which combined with C2 below is a real account-takeover path.
- **How:** derive client IP from a trusted position — Cloudflare `CF-Connecting-IP`, or `X-Forwarded-For[-N]` where N = number of trusted proxies, or Starlette `ProxyHeadersMiddleware` with a known-proxy allowlist. Never trust the leftmost XFF. Add a per-phone counter on `send-otp`.

### C2 — 4-digit login OTP is below industry standard *(HIGH, pairs with C1)*
`dependencies/__init__.py:48-52` (`OTP_LENGTH = 4`) — 1/10,000 vs 1/1,000,000 for 6 digits. Login is SMS-OTP only (no password), so the OTP is the sole factor. Mitigations (5-fail/24h phone lockout) hold *only while per-IP throttling works* — and C1 removes that layer.
- **How:** move login OTP to 6 digits (pickup OTP can remain 4). Low effort, 100× odds improvement.

### C3 — RLS UPDATE policies allow unrestricted self-column writes *(HIGH)*
`supabase_rls.sql:27-46` — `users_update_self` / `drivers_update_self` gate only on row ownership, with **no column restriction**. If any anon-key + Supabase-Auth path is reachable, a rider could `PATCH` `users.role='super_admin'` (non-admin role is re-read from the row every request → instant privilege escalation), and a driver could self-set `is_available`/`is_verified`, bypassing the `go_online` document-expiry / insurance checks.
- **Caveat:** these key on `auth.uid()`, which is NULL for Spinr's self-minted JWTs — if no Supabase-Auth client path exists, the policies deny-all (safe but theatrical). **Verify reachability first.**
- **How:** restrict writable columns via column privileges or a `WITH CHECK` trigger that pins immutable fields (role, status, token_version, verification, money).

### C4 — `drivers` table fully readable by anon key *(HIGH)*
`supabase_rls.sql:39-41` — `drivers_select_public … USING (true)` exposes **every column of every driver row** (encrypted address, license, plate, earnings, ratings) to any anon-key SELECT, not the "nearby driver" projection intended. PIPEDA data-minimization violation + bulk PII harvest vector.
- **How:** replace with a restricted view exposing only lat/lng/vehicle-type/rating for *online* drivers. Also confirm `refresh_tokens`, `audit_logs`, wallets, payments, `corporate_*`, `driver_insurance_periods`, `otp_records` all ship RLS in their migrations (the central file covers only ~10 tables).

### C5 — Secure posture is fail-*open*, gated on `ENV == "production"` *(MEDIUM-HIGH)*
Every hardening guard (weak-secret/JWT-length check, CORS wildcard block, static-OTP "1234" bypass, HSTS, App Check) is conditioned on an exact string match, and `ENV` defaults to `"development"` (`config.py:164`). A deploy that forgets `ENV=production` silently runs with the **static-OTP bypass live** (log in as any phone), no weak-secret enforcement, App Check off.
- **How:** default to the secure posture; require explicit opt-out for dev, or refuse to boot on a public interface without `ENV` explicitly set.

**Also flagged (secondary):** production CORS unconditionally trusts `localhost:3000/3001` with credentials (`middleware.py:530-540`); reviewer fixed-OTP allowlist (`config.py:277-298`) is a standing prod backdoor if not cleared — add per-entry expiry.

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

The codebase's own rule — *never `logger.warning(...)` and continue on a DB/auth/payment error; surface loudly* — is well-followed on the request path but **violated in the Redis/infra layer and one client component.**

### T1 — Redis client fails *open* / *silent*, disarming security controls *(HIGH — highest leverage)*
- `redis_client.py:145-163` — `redis_set_nx` on a Redis error logs `warning` then falls through to the in-process dict and returns `True`, so during a Redis blip **every replica "acquires" the leader lock**. `reconciliation.py` and `stripe_reconcile.py` use this as their *sole* single-run guard → duplicate daily finance runs + duplicate discrepancy alerts.
- `redis_client.py:80` — the client reads **only** `REDIS_URL`, but the boot check passes if *any* of `REDIS_URL`/`RATE_LIMIT_REDIS_URL`/`WS_REDIS_URL` is set. Configure only `RATE_LIMIT_REDIS_URL` (documented, supported) → **green boot** while OTP lockout, retry budget, and all leader locks silently run on the per-process dict.
- `redis_client.py:92-94` — initial-connect failure logs `warning` + returns `None` (silent dict fallback), inconsistent with the connected-then-fails path which correctly `logger.error` + raises.
- **How:** on the Redis-error branch, fail **closed** for lock acquisition (return `False`); resolve the effective URL like WS does; `logger.error` + emit `spinr_redis_unavailable_total` and a fallback-mode gauge. **Fixing T1 as a unit is the single highest-leverage change in this review.**

### T2 — WS fan-out degradation in prod only logs WARNING *(HIGH)*
`core/lifespan.py:496-506` — when WS pub/sub fails to start in prod (the exact P0-B3 failure the module exists to fix), it logs `warning` and proceeds. With `min_machines_running=2`, rider and driver land on different machines → dispatch offers, `ride_taken`, and status changes silently vanish cross-replica, with no metric/Sentry/alert.
- **How:** `logger.error` + Sentry (domain=`dispatch`) + `spinr_ws_fanout_degraded` gauge wired to alerting; the `pubsub.status().active` signal already exists.

### T3 — Durable outbox write failure returns success → reconnect replay silently drops events *(BUG)*
`utils/ws_pubsub.py:184-210` — if the seq/outbox pipeline raises, it logs, falls back to an *unwrapped* message, still publishes, and returns `True`. The event is delivered live but never lands in the replay ring, so a client reconnecting with `?last_seq` will not get it replayed — defeating reconnect recovery for exactly the durable events it protects (offers, `ride_taken`).
- **How:** distinguish "delivered live but not persisted" from "durable"; return an actionable status or emit `spinr_ws_outbox_write_failed_total`.

### T4 — Raw error message shown to end users *(BUG — client)*
`shared/components/ErrorBoundary.tsx:40-42` — `ErrorFallback` renders `{error.name}: {error.message}` unconditionally; only the *stack* is `__DEV__`-gated. Users see "Cannot read property 'lat' of undefined", and a thrown message can echo an address/phone fragment (PII-in-UI). Directly violates the "no raw errors to users" rule.
- **How:** generic localized string in production; render `error.message` only under `__DEV__`.

### T5 — Loop watchdog covers 16 of 24 loops; safety/money loops unmonitored *(TELEMETRY)*
`core/lifespan.py:403-445` — docs say "16 loops"; there are actually **24**. The hand-maintained `_WATCHDOG_LOOP_NAMES` omits `safety_checkin` (T&S incidents), `preauth_capture` (lapsed card holds = lost revenue), `driver_claim_reaper` (supply erosion), `reconciliation`, and others. `_restartable` catches crashes, not *hangs*, so a wedged safety loop is unalerted.
- **How:** derive the watchdog list from the actual spawned-task registry so new loops are covered by default.

### T6 — Split logging (loguru + stdlib) with an incomplete Sentry-tag bridge *(TELEMETRY)*
`server.py:451-473` promotes only loguru `extra` to Sentry tags; stdlib modules (`payment_retry.py`, `redis_client.py`, `rate_limiter.py`) reach Sentry only via `LoggingIntegration`, so their `extra={"domain":"payments","ride_id":...}` likely never becomes Sentry `domain`/`ride_id` tags. A money-critical loop's errors lose their domain context.
- **How:** add a loguru `InterceptHandler` on the root stdlib logger (or standardize on one system).

---

## 🐢 Performance Bottlenecks & Optimizations

### P1 — Greedy per-ride matching; no batched/global assignment *(the core Uber/Lyft gap)*
`routes/rides.py:1144-1164` — each request independently claims the nearest N drivers via first-come `claim_driver_atomic`. Under concurrent demand two requests race for the same drivers; whichever fires first wins, so ride B can be assigned a far driver while its ideal driver was grabbed by ride A → globally sub-optimal ETAs, lower match rate, breaches "utilization ≥55%" and "P95 offer→accept <2s" under load. ETA is real-time Distance Matrix only with a crude flat-30km/h haversine fallback (`rides.py:1140-1142`), no predictive model.
- **How:** windowed batch matching — accumulate requests over a short interval and solve the assignment globally (Hungarian / min-cost flow), as Uber/Lyft do.

### P2 — N+1 per-driver enrichment inside the <2s dispatch hot path
`routes/rides.py:1268-1307` — the notify loop runs a separate `quest_progress` select and offer-card build for *each* claimed driver, serially, on the latency-critical path. Shared fetches were parallelized (`:1248`) but quest progress was not.
- **How:** batch quest progress with one `.in_(driver_uids)` query before the loop.

### P3 — WS receive loop processes integrity + breadcrumb + fan-out inline before reading the next frame
`routes/websocket.py:654-810` — each inbound location frame awaits integrity check, DB write, Redis cache, active-ride resolve, breadcrumb buffer, and per-ride fan-out *before* the next `receive_text()`. GPS ingest latency is coupled to downstream processing with no backpressure beyond the rate limiter; a slow Maps/DB/Redis stalls that socket.
- **How:** hand location frames to a bounded per-connection worker/queue, drop-oldest under pressure (Uber decouples ingest from processing).

### P4 — Surge supply count is a province-wide driver scan
`utils/surge_engine.py:160-195` — demand is area-scoped but supply fetches **all** online+available drivers (cap 5000) then filters by polygon in Python, per area, every 2 min. The 5000 truncation over-prices a *regulated* fare at scale. The flag-gated PostGIS path (`_count_supply_spatial`) exists but defaults off.
- **How:** roll out the spatial RPC; until then the cap-hit is a real over-price risk.

### P5 — Client re-render storm on high-frequency map screens
`rider-app/app/ride-in-progress.tsx:60`, `driver-arriving.tsx:58` subscribe to the **entire** store; `updateDriverLocation` fires ~every 3-4s (`useRiderSocket.ts:84-94`), re-rendering the whole screen (map, sheets, fare panel) instead of just the marker. 42 full-store subscriptions across the two apps.
- **How:** selector subscriptions (`useRideStore(s => s.currentDriver)`); isolate the marker into a memoized child.

**Also:** metrics are per-process, in-memory, unbounded, never aggregated (`utils/metrics.py`) — `/metrics` reflects one worker; with 2-4 workers × 2 machines the SLO P95 tables can't be computed reliably. Move to `prometheus_client` multiprocess mode + cardinality guard.

---

## 💡 Tech Stack & Architecture Recommendations

| Gap | Why it's a problem | Recommended direction |
|---|---|---|
| **No double-entry ledger** (`webhooks.py` refund/dispute; `payment_service.record_payment_event`) | Refunds/chargebacks mutate `ride.payment_status` one-sidedly; in the 0%-commission model the driver already banked 100%, so a chargeback is absorbed with **no ledger reversal** and no clawback. Ledger and true cash diverge. | Append-only **double-entry ledger** (debit/credit pairs); every reversal is a balancing entry. Reconciliation becomes provable, not status-derived. |
| **Greedy matching, no predictive ETA** (P1) | Sub-optimal assignment + no historical ETA caps match rate/utilization | Batched assignment solver + historical/ML ETA (h3 geo-indexing, min-cost matching) |
| **No distributed tracing / correlation IDs** | Can't stitch dispatch→fare→settlement across replicas; per-process metrics | **OpenTelemetry** traces + request-scoped correlation IDs propagated into logs + Sentry; move Prometheus to multiprocess/pushgateway or a real exporter |
| **Symmetric JWT (HS256) + secrets in `.env`/`app_settings`** (`config.py:37-49`) | Anyone who reads `JWT_SECRET` can mint admin tokens; DB read leaks live Stripe/Twilio creds | **RS256/ES256** asymmetric signing (only auth holds the private key) with `kid` rotation; secrets from Vault / Fly secrets / AWS Secrets Manager; encrypt `app_settings` credential columns at rest |
| **Presence 100% Redis-dependent, fail-open to ghost drivers** (`dispatch_service.py:337-355`) | On a Redis outage, offers route to backgrounded/killed phones that won't ring | Circuit-breaker + SLO alert on `spinr_dispatch_presence_filter_failed_total`; short DB-side `last_seen_at` fallback |
| **Parallel Railway+Fly deploy from `main`, ungated, no migration gate** (`.github/workflows/deploy-*.yml`) | Providers can serve **different code versions against shared DB+Redis**; schema-dependent change can hit old code on a migrated DB | Gate the two deploys (or alert on build-SHA skew via `/health`); apply schema-affecting migrations in a gated expand/contract step before cutover; blue-green + WS connection draining |
| **`UVICORN_WORKERS` unpinned on Railway** (`railway.json:8` defaults 4 vs Fly's 2) | 4 workers × 24 loops = **96 loop copies/machine** on shared-cpu-1x/1gb; multiplies duplicate reconciliation blast radius | Pin `UVICORN_WORKERS=2` on Railway; longer term, gate background loops to a single dedicated worker rather than N copies/machine |
| **No shared circuit breakers for Stripe/Twilio/FCM** | Supabase breaker is solid but per-process; upstream stalls have no breaker | Add per-upstream circuit breakers (already have the pattern) |
| **Client "offline-first" is dead code** (`shared/api/offlineQueue.ts` has zero callers) | Persistence+replay infra exists but is inert → false confidence; dropped cancel/rating/wallet action just fails | Wire `enqueueRequest` into `client.ts` catch path; `initOfflineQueue()` in both `_layout.tsx`; idempotency-key the replays |

---

## 🛠️ Maintainability & Code Smells

- **`routes/rides.py` is 6,398 lines** — a god file mixing state machine, dispatch, offer timeout, matching, and enrichment. High-centrality per graphify. Split into `services/dispatch/`, `services/ride_state/`, and thin route handlers. (Sprint marks it "do not touch broadly" — refactor needs its own ticket + broad review.)
- **Two divergent offer-timeout handlers** — `_offer_timeout_handler` (30s, checks `driver_assigned`, admin-assign path) vs `_batch_offer_timeout_handler` (15s, checks `searching`, normal dispatch) (`rides.py:1403` / `:1577`). Different TTLs and state assumptions; a future TTL-policy change will likely update only one. Unify into one parameterized handler.
- **Two wallet settlement totals** — `/wallet/pay` debits `total_fare` (`wallet.py:249-254`) while the automatic path and card path use `grand_total` (see money findings). Divergent totals = reconciliation hazard; route both through one helper.
- **Stale docs** — CLAUDE.md says "16 background loops"; there are 24. The migration-count note and watchdog list are hand-maintained and drift. Derive from source of truth.
- **Split logging systems** (T6) and **per-process in-memory metrics** are maintainability debt as much as telemetry debt.
- **Rider WS trusts `any` payloads** (`useRiderSocket.ts:71`) while driver-app has `_toFiniteCoord()` clamping — inconsistent robustness for the same event type; share one validator.

---

## 🧪 Testing & QA (Missing Edge Cases)

**Strength:** backend test breadth is genuinely strong — 303 files including `test_ride_state_machine.py`, `test_webhook_stripe_v15.py`, `test_c2_driver_cancel_atomic.py`, `test_e2e_rating_regression.py`, `test_fare_split.py`, `test_surge_engine.py`, corporate/decimal suites. Backend coverage is **not** the gap.

**The gap is client E2E enforcement and specific money/dispatch edge cases:**

- **Mobile E2E specs exist but never run in CI** — `rider-app/e2e/{ride-booking,cancellation,payment-completion,rating-submission}.spec.ts` and `driver-app/e2e/*` are written, but `.github/workflows/ci.yml` runs Playwright only for admin-dashboard (and even that is `continue-on-error` on PRs). The core ride lifecycle, cancellation race, payment completion, and first-rating flows have coverage that **is not enforced**. → Add blocking rider/driver Playwright jobs; graduate admin E2E off `continue-on-error`.
- **Missing regression tests to write for the findings above:**
  - Wallet `/pay` collects `grand_total`+tip (not `total_fare`) — tax-collection assertion (money finding M1).
  - Top-up credits `min(amount_received, metadata)`, and same-minute different-amount top-ups don't collide (M2).
  - Partial refund → `partially_refunded` (not `refunded`) + ledger reversal exists (M3).
  - XFF-spoof cannot bypass `send-otp` throttle; per-phone backstop (C1).
  - Stuck-ride sweeper expires pending offers + notifies offered drivers (dispatch finding D8).
  - Offer-timeout re-dispatch increments the attempt counter (D4).
- **Ride-booking idempotency key regenerates per attempt** (`rideStore.ts:672` uses `Date.now()`) — a user re-tap or offline replay mints a new key → second ride/pre-auth. Test: same booking intent → same key across retries.

---

## Money-Layer Findings (feed the plan; detail for the payments team)

- **M1 (HIGH, tax):** `/wallet/pay` validates/debits `total_fare`, not `grand_total` — GST(5%)/PST + area fees + tip **uncollected** on wallet-settled rides (`wallet.py:249-254`). Regulatory GST obligation. Route both wallet flows through one helper on `grand_total`.
- **M2 (HIGH):** wallet top-up webhook credits `metadata.amount_cad` without checking `amount_received` (`webhooks.py:508-534`), and the idempotency key omits the amount → same-minute different-amount top-ups collide. Corporate path already fixed this; rider path didn't. Credit `min(amount_received, metadata)` + amount-in-key.
- **M3 (HIGH):** `charge.refunded` marks the whole ride `refunded` on any *partial* refund and never reverses driver earnings (`webhooks.py:788-829`); disputes lost (`:946`) likewise post no reversal. Compare refunded-vs-owed → `partially_refunded`; post a compensating `financial_events` row + (policy) driver clawback.
- **M4 (MED):** manual surge >2.5× is silently clamped in fare paths (`fares.py:204-208`, `fare_service.py:383-387`) though admin UI accepts 1.0–10.0 — misleading; make the policy explicit.
- **M5 (MED, tax):** legacy `grand_total is None` fallback to `total_fare` charges **tax-free** on any in-flight ride lacking `grand_total` (`payments.py:72-76`, `payment_service.py:237`). Backfill `grand_total`.
- **M6 (LOW, PIPEDA):** wallet top-up sends rider email + legal name to Stripe (US) (`wallet.py:169-171`), defeating the data-minimization the payments path deliberately keeps (`payments.py:127-138`). Send only `metadata.user_id`.

---

## 📈 Manager's Verdict

**Overall code health: B+ — "production-capable, pre-scale."**

This is a well-run engineering effort. The team demonstrably understands its own domain: money is `Decimal`, Stripe is idempotent, auth has token rotation + reuse detection, the ride-state machine is guarded and tested, and device token storage is correct. A prior P0 sprint was executed and closed. Very little of this review is "you did it wrong"; most of it is "you built the safe path and left the *degraded* path quietly unsafe."

The through-line across the three highest findings is a **single cultural anti-pattern that contradicts the codebase's own stated rule**: in a handful of infrastructure seams, the system *keeps serving quietly* (Redis fail-open, ENV-gated hardening, WS-degradation warnings, silent outbox drop) in exactly the spots CLAUDE.md says to *fail loudly*. Fixing that posture — fail closed on the security-critical Redis paths, page on WS degradation, secure-by-default on ENV — closes most of the real risk with modest effort.

Against Uber/Lyft, Spinr is **feature-competitive but architecturally pre-scale**: greedy matching instead of batched assignment, status-string accounting instead of a double-entry ledger, per-process metrics instead of distributed tracing, and a genuinely inert offline layer. None of these block a controlled Saskatchewan launch; all of them are what you'd address before multi-city scale.

**Recommended sequencing:**

1. **This week (CRITICAL/HIGH, low effort, high risk-reduction):** C1 (XFF/IP + per-phone OTP backstop), C2 (6-digit OTP), C5 (ENV secure-by-default), T1 (Redis fail-closed + URL resolution), T4 (client raw-error leak), M1/M2/M3 (wallet tax + top-up + refund accounting). Each is scoped, testable, and independently shippable.
2. **This sprint (HIGH/MED):** C3/C4 (RLS write/read scope — verify anon-key reachability first), T2 (WS-degradation paging), T5 (watchdog from registry), P2 (batch quest N+1), P5 (client selector subscriptions), enforce mobile E2E in CI.
3. **Roadmap (architecture, ticketed):** double-entry ledger, batched/predictive matching, OpenTelemetry + multiprocess metrics, RS256 + secrets manager, Railway/Fly deploy gating + migration expand/contract, wire the offline queue.

**Do-not-touch reminder:** the sprint file quarantines `routes/rides.py` state-machine paths and `admin authStore.ts` — the P1/P2 dispatch and refactor items there need their own tickets and broader review before anyone opens the file.

---

*Generated read-only. No source files were modified. Line numbers reflect the repo state at the review commit; re-verify before acting as active branches move.*
