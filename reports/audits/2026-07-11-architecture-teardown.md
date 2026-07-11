# Spinr — Comprehensive Code & Architecture Teardown

**Date:** 2026-07-11 · **Type:** Read-only director-level review · **Scope:** backend, rider-app, driver-app, admin-dashboard, shared, infra · **Comparators:** Uber / Lyft engineering patterns

> **Method:** four parallel grounded reviews (backend correctness, security/PII/compliance, frontend/mobile, architecture). Every finding carries `file:line` evidence. Every item the current sprint claimed "shipped" (HttpOnly admin cookies, admin TTL 1h, GPS OOM fix, first-rating crash, payment-state guard, SOS retry, Firebase audience binding, `_vault_encrypt` fail-closed, logger.warning sweep) was **independently verified as genuinely done** and is **not** re-reported here. Only currently-present issues appear below.

---

## Headline

This is a **well-hardened, above-average codebase for its stage** — atomic DB-level state transitions, server-authoritative payment amounts, a mature retry/circuit-breaker DB layer, disciplined replay-safe background loops, and an excellent shared 401/refresh interceptor. It is *not* a "generic Uber clone with bugs." The real risk is concentrated in **three places**: (1) financial/regulatory background jobs under the dual-platform deploy, (2) the data-access throughput ceiling, and (3) a handful of specific latent defects. Address the Critical infra item before any traffic growth; the rest is a well-ordered backlog.

---

## 🚨 Critical Issues & Security Flaws

| # | Severity | Issue | Evidence | Why it matters | Fix |
|---|---|---|---|---|---|
| C1 | **Critical (infra)** | Background loops run on **every replica AND on both deploy platforms (Railway + Fly) simultaneously**. Redis coordination hangs off `redis.spinr.ca`, a DNS alias *repointed on fail-back*. During DNS propagation the two platforms can target **different Redis instances** → leader locks (reconciliation, T4A issuance, Stripe reconcile, payment retry) stop mutually excluding. | `core/lifespan.py` (~24 loops via `_spawn`); CLAUDE.md deploy section; leader-lock loops keyed on the movable Redis | Double-fired **money/tax** side-effects: duplicate T4A notices, double payment-retry charges, reconciliation races. Regulatory + financial. | Run the standby **web-only** (`RUN_BACKGROUND_LOOPS=false`), OR pin all leader locks to a single Redis that never moves (separate from WS/cache Redis). Long-term: move financial/scheduled workflows to Temporal so exactly-once is a platform guarantee, not a DNS-timing hope. |
| C2 | High (PIPEDA) | **Rider address text written to logs.** Autocomplete `input` and prediction `description[:60]` logged at INFO — and the loguru→Sentry bridge makes them searchable. CLAUDE.md explicitly bans "exact pickup/dropoff addresses — log city/area only." | `routes/maps_proxy.py:114-120`, `:147-151` | Anyone with log/Sentry access can reconstruct where a rider is going, time-correlated to the user. PIPEDA violation. | Drop the `input`/`description` values (log result count + `restricted` bool only), or geohash/city-truncate. |

**Not critical but worth naming under "security flaws":** admin edge middleware (`admin-dashboard/src/middleware.ts:117-140`) validates JWT *structure + `exp`* only, no signature (Edge-Runtime limit), and `api/auth/set-cookie` accepts an arbitrary token body — a forged unsigned `super_admin` JWT gets served the **dashboard shell**. Impact is bounded (every backend API re-verifies the HS256 signature, so no data leaks), but it's a real "serves protected UI to an unauthenticated forger" gap. Fix: verify signature via a lightweight backend `/verify` before rendering protected pages, or formally accept the standard-SPA tradeoff.

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**Strong overall** — DB/Stripe failures consistently `logger.error(..., exc_info=True)` + clean 502/503; no silent-success fallbacks; the PCI raw-card perimeter rejects PAN fields pre-parse. Remaining gaps:

- **Emergency actions silently lost (frontend HIGH).** `rideStore.syncOfflineRequests` reads/clears `AsyncStorage['offline_queue']`, but **nothing ever writes to it** — offline booking/cancel/tip/**emergency** are never actually queued despite a full replay+retry+"Offline Actions Lost" UX implying they are. `rider-app/store/rideStore.ts:521-603`. *This is a dead safety path.* Fix: wire an enqueue producer (or delete the flow) and add the missing `Idempotency-Key` to the replay `POST /rides` (`:561` vs live `:673-675`).
- **Silent wallet failures → `[object Object]` to the user.** `walletStore.fetchWallet/fetchTransactions` swallow errors into state that isn't rendered (blank wallet, no retry); `topUp/payWithWallet` read `error.response.data.detail`, which for validation errors is an **array** → renders "[object Object]". `shared/store/walletStore.ts:85-88,125-127`. Fix: render the error state with retry; normalize `detail` array → first message.
- **DB outage masked as a schema mismatch.** Rider cancel attribution write catches *any* `Exception` and "retries minimal" with only `logger.warning` — unlike its sibling `cancel_scheduled_ride` which first verifies a genuine missing-column/PGRST204. `routes/rides/cancellation.py:283-285` (cf. `:477-492`). A real DB outage surfaces via the wrong path, noisily. Fix: mirror the column-check gate before the minimal retry.
- **Admin raw-error `alert()`s** leak `e?.message` to operators via `window.alert`: `venues/page.tsx:87,95`, `ride-detail-modal.tsx:1013`, `sidebar.tsx:211`. Functional but jarring; route through the app's toast/error UI.
- **Telemetry gap: no distributed tracing.** Sentry + Prometheus are solid, but there's **zero OpenTelemetry** — no single trace spanning API → threadpool DB → Redis → WS → Stripe. When the "offer→accept P95 < 2s" KPI regresses you can't see which hop owns it. (See Tech Stack §H3.)

---

## 🐢 Performance Bottlenecks & Optimizations

| # | Sev | Bottleneck | Evidence | Fix |
|---|---|---|---|---|
| P1 | **High** | **Synchronous Stripe SDK calls block the asyncio event loop.** The hot paths (`create-intent`, `payment-sheet`, `confirm`) correctly use `asyncio.to_thread` — but they all first `await get_or_create_stripe_customer`, which calls `stripe.Customer.create` **inline, unthreaded**. One slow Stripe round-trip freezes every concurrent request on that worker. | `routes/payments.py:134` (+ `:501,539,599,741,745,758,811,854`) | Wrap **every** `stripe.*` call in `asyncio.to_thread`, as the intent paths already do. |
| P2 | Med | **N+1 in the dispatch offer fan-out** (`<2s` SLA path): a `quest_progress` select runs once per claimed driver (up to 10 sequential round-trips) while building offer payloads. | `routes/rides/matching.py:748` | One batched `.in_(driver_uids)` query before the loop, indexed by driver. |
| P3 | Med | **Location-update disk-write storm (mobile).** Full ride+driver JSON serialized to `AsyncStorage` on **every** WS `location_update` (every 1–3s for the whole trip). Battery drain + UI jank on the hottest path. | `rider-app/store/rideStore.ts:1037` | Throttle persistence to ≤ every 10s; skip persisting coordinate-only deltas. |
| P4 | Med | **Data-access throughput ceiling.** Every DB call is a synchronous `supabase-py` REST request offloaded to a 64-thread pool; load-test already shows "DB thread pool saturated, P95 612ms" at 80 concurrent. Thread-per-blocked-query on a 1GB VM. | `repositories/_base.py`, `_DB_EXECUTOR` | Move hot paths (dispatch fetch, ride reads, fare settlement) to **asyncpg via the Supabase transaction pooler (:6543)**; point admin analytics at a **read replica**. (See §H1.) |
| P5 | Low | Float used for **display** incentive/quest figures in the dispatch payload (persisted claim is correctly Decimal, so no financial impact — but violates the Decimal convention and can show a cent-drifted bonus). | `routes/rides/matching.py:695-707, 759-766` | Compute with Decimal, convert only at the JSON boundary. |

---

## 💡 Tech Stack & Architecture Recommendations

**Strengths to preserve:** half-open circuit breaker + policy-gated retries + Redis token-bucket retry budget in the DB layer; genuinely disciplined replay-safety (atomic DB claim / `reminder_sent` / idempotency key / Redis leader lock per loop); Uber-style Redis presence heartbeat composed with DB `is_online` intent + a Socket.IO-adapter-style pub/sub with per-client seq/outbox replay on reconnect.

| # | Gap | Uber/Lyft reference | Recommendation & migration path |
|---|---|---|---|
| H1 | **PostgREST-over-HTTP/2-through-a-threadpool** is the throughput wall (see P4). PostgREST also can't do ergonomic row-locking, forcing money mutations into `SECURITY DEFINER` RPCs. | Both run async connection-pooled Postgres access. | Adopt **asyncpg + Supabase transaction pooler / PgBouncer** for hot paths (real async I/O, no thread-per-query). Add a **read replica** for dashboards. Highest-leverage 10x unblocker. |
| H2 | **Naive geospatial.** Dispatch fetches ≤500 online drivers in a bbox then filters/ranks in Python; surge pulls ≤5000 rows and runs point-in-polygon per tick. `matching.py` **already logs when the 500-cap truncates** — at density the nearest driver can sit in row 501 and go unmatched; surge (a *regulated* price) can be mis-counted. A PostGIS RPC exists but is **flag-gated off**. | Uber **H3** hex-indexing; **batch-matching windows** (accumulate ~1–2s, solve global assignment) vs greedy nearest. | Enable PostGIS `ST_DWithin`/`ST_Covers` + **GIST index** for dispatch & surge (RPC already written — rehearse, flip `SURGE_SPATIAL_COUNT`). At real scale, H3 bucketing + batch matching. |
| C2/queue | **No durable message queue / event bus.** Dispatch offers, payment retries, Twilio/Stripe, notifications are `asyncio.create_task` fire-and-forget backstopped by polling sweepers. Works at SK scale; at 10–100x the polling loops become the latency floor and hammer Supabase — no backpressure, DLQ, or retry visibility. | Uber/Lyft: durable workflow engines + event streams. | **Temporal** for payment/dispatch workflows (durable, exactly-once, built-in retries/timeouts — replaces offer-timeout, payment_retry, preauth_capture, reconciliation loops). Or minimally **Redis Streams / SQS** for notification & webhook fan-out. Migrate **payment retry first**. Also resolves C1's exactly-once. |
| H3 | **No distributed tracing** — only an OTel-compatible `X-Trace-ID` header, no spans/SDK. | Both run full OTel/Jaeger-class tracing. | **OpenTelemetry** auto-instrumentation (FastAPI + httpx + redis) → Grafana Tempo / Honeycomb; propagate `trace_id` into existing Sentry tags + WS logs. |
| M1 | Single WS pub/sub channel `spinr:ws:dispatch` — fan-out is O(replicas × all-messages). Fine now (hundreds of sockets); frays in the low thousands. | — | Pattern-subscribe per-role/per-geo when socket count crosses ~single-thousands (no migration needed). |
| M2 | **No feature-flag system** — rollout gating is ad-hoc env vars needing redeploy. | Both run mature flag platforms. | **GrowthBook** (OSS, self-hostable, Canadian-residency-friendly) for progressive dispatch/surge rollout. |
| M3 | **DNS-only failover, no load balancer** — manual single DNS change; TTL/client caching = minutes of downtime, and it underpins C1's split-brain. | LB with health checks + automated failover. | **Cloudflare Load Balancer** with health checks for sub-minute automatic failover; formalize which side owns background loops. |

---

## 🛠️ Maintainability & Code Smells

- **Dead + broken shared infra.** `shared/api/cachedClient.ts` and `shared/api/offlineQueue.ts` are imported nowhere; worse, `cachedClient.getStoredToken` reads a persisted `auth_token` that `authStore` keeps memory-only and explicitly deletes (`authStore.ts:247`) — every cached request would 401 if re-enabled. Remove or fix.
- **Wallet re-drive Redis lock leaks on failure.** `_wallet_redrive_lock_key` (`SET NX`, 30s TTL) acquired at `routes/rides/payments.py:289`, released only at `:399` — *after* the failure `raise` at `:386`. A failed re-settlement holds the lock for the full 30s → rider retry gets 409 for 30s. Move release into a `finally`.
- **Residual `any` in mobile hot paths** (post-sweep leftovers): `driverStore.ts:705,752,753` (active-ride/offer path), `rideStore.ts:494-507` (promo matching). Bypass the typed models the sweep established.
- **Stale top-level docs.** `ARCHITECTURE.md`/`DEPLOYMENT.md` describe Railway-only, no Fly, no Redis — contradict the authoritative CLAUDE.md. A new engineer reading them gets the wrong mental model of the exact system (C1) that most needs an accurate one. Refresh.
- **Admin cookie 8h vs 1h token TTL.** `set-cookie/route.ts:4` `COOKIE_MAX_AGE = 8h` with a stale "session TTL" comment; token now expires in 1h (cookie is inert after, but the dead 8h window will mislead the next hardening pass). Set `maxAge ≈ 1h`.

---

## 🧪 Testing & QA (Missing Edge Cases)

The state-machine, fare-branch, and E2E (cancellation, payment-guard, SOS regression) suites are genuinely good. Gaps that map to the findings above — each should ship *with* its fix:

1. **Offline queue** — a test asserting an offline action is enqueued *and* replayed with an idempotency key (would have caught the never-written queue immediately).
2. **Stripe-call threading** — an async test asserting no `stripe.*` call runs on the event loop (e.g. patch `stripe.Customer.create` to sleep, assert other requests aren't blocked).
3. **Wallet re-drive lock** — assert the lock is released after a *failed* re-settlement, not just a successful one.
4. **maps_proxy PII** — assert the autocomplete log line contains neither `input` nor `description`.
5. **Split-brain (C1)** — an integration test with two "replicas" on distinct Redis instances asserting a leader-locked job fires once (documents the invariant even if the fix is env-flag).
6. **Validation-error rendering** — assert an array-shaped `detail` renders a readable message, not `[object Object]`.
7. **Accessibility (regulatory).** Only 17/58 rider and 11/54 driver `.tsx` files reference any `accessibilityLabel`/`accessibilityRole`; core booking/payment/SOS screens likely lack screen-reader labels. WCAG 2.1 AA is a stated SK obligation — add an a11y lint gate + audit interactive controls on customer surfaces.

---

## 📈 Manager's Verdict — Overall Code Health

**Grade: B+ / strong for stage.** The consumer-ride happy path and the money/auth perimeter are well-engineered and appropriately scaled for a Saskatchewan launch. Discipline is visible and consistent: DB-level atomic transitions, server-authoritative charges, idempotent webhooks, replay-safe loops, PII-redacted client error buffers, single-flight token refresh. The team's own sprint claims all check out — that reliability of self-reporting is itself a health signal.

**Where a director spends the next cycle, in order:**

1. **C1 split-brain (this week, no traffic-growth without it).** Ship `RUN_BACKGROUND_LOOPS=false` on the standby *or* pin leader-lock Redis. Cheapest possible insurance against duplicate charges/T4As. Env-flag, hours of work.
2. **C2 maps_proxy PII (this week).** One-line-class fix closing a live PIPEDA exposure. Cheap, regulatory.
3. **Frontend H1 offline/emergency queue (this sprint).** Either implement the producer + idempotency key or delete the misleading dead safety path — do not leave a UX that lies about queuing emergencies.
4. **P1 Stripe threading (this sprint).** Wrap remaining `stripe.*` in `to_thread`; removes a self-inflicted event-loop stall from the payment path.
5. **10x readiness (next quarter):** asyncpg+pooler (H1) → PostGIS/GIST for dispatch+surge (H2) → OpenTelemetry (H3) → Temporal for payment/dispatch (C2/queue). This is the ordered path from "works at SK scale" to "provably correct under growth."

**Net:** no fire alarm in the product code; one architectural landmine (C1) that is cheap to defuse today, one live compliance leak (C2), and a clean, well-prioritized runway to scale. Maintainability and readability are high; the biggest maintainability tax is stale top-level docs describing a simpler system than the one that's running.
