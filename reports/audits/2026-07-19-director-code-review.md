# Spinr — Engineering Director Code & Architecture Review
**Date:** 2026-07-19 · **Scope:** backend hot paths, security/PII, client surfaces, architecture/tooling · **Type:** read-only teardown + plan · **Benchmark:** Uber/Lyft-class platforms

> Method: four parallel senior reviewers over the real code (not the audit docs), with the two highest-severity findings hand-verified against source by the reviewing director. Findings carry `file:line`, impact ("why"), and a conceptual fix ("how") — **no code was changed**.

---

## 📈 Manager's Verdict (read this first)

**Health: strong. This is a disciplined, unusually well-hardened pre-launch monolith — not a prototype.** The engineering maturity is real and in the right places: Decimal-only money end-to-end, atomic compare-and-set ride acceptance, leader-locked idempotent background loops, a durable offer-expiry backstop, daily Stripe↔DB↔wallet reconciliation, PIPEDA residency gating, a 348-file test suite with E2E tiers on a 60%→80% coverage ratchet, and 21 CI workflows (SAST, secret-scanning, migration safety, lockfile drift) that exceed what many Series-B companies run. A central exception handler sanitizes all 5xx bodies to `ERR_*` sentinels and correlates them to a request-ID in the logs. **The team clearly understands its own architecture's failure modes and has engineered around them.**

The gaps are not "it's built wrong." They cluster in three themes:
1. **Disclosure/ledger correctness on the money path** — one real fare-attribution bug that makes the receipt disagree with the payout ledger (serious for a "0% commission / no hidden fees" brand).
2. **PII log hygiene has one hole** — the Maps proxy logs raw addresses + coordinates at INFO, contradicting the discipline enforced everywhere else.
3. **Operational depth for launch** — no distributed tracing, no feature-flag/kill-switch layer, no real staging env, and the PostgREST-over-HTTP DB access will be the first thing to hit a wall under concurrency.

**Verdict: green-to-launch after the P0/P1 list below.** Do **not** let anyone use this review to justify a microservices/Kafka/Temporal rebuild — that would be over-engineering a product that hasn't launched. The right next investments are additive (tracing, flags, async DB pool), not structural.

---

## 🚨 Critical Issues & Security Flaws

| # | Sev | Finding | Location | Why it matters | Fix (conceptual) |
|---|-----|---------|----------|----------------|------------------|
| C1 | **HIGH** | **Fare uplift misattributed — receipt disagrees with payout ledger.** On a minimum-fare ride the "Ride fare" line = `total − surge − booking − airport` and is disclosed as *"Driver earns 100%"*, but stored `driver_earnings = base+distance+time` (the un-clamped subtotal). The `minimum − subtotal` delta is allocated to nobody. **Verified.** | `services/fare_service.py:210-213`, `:266-275` | Ledger doesn't reconcile; the rider-facing receipt claims driver income the payout record never books. Direct hit on the "no hidden fees / 0% commission" brand promise and on T4A earnings accuracy. | Explicitly assign the `minimum − subtotal` uplift to `driver_earnings` (or a named, disclosed platform line) so `charged == driver_earnings + admin_earnings` always holds; add a reconciliation assertion + regression test. |
| C2 | **HIGH** | **Raw rider search text + GPS logged at INFO.** Autocomplete proxy logs `input=%r` (home/work addresses, people's names) and raw `lat,lng`. **Verified.** | `routes/maps_proxy.py:114-120` | PIPEDA violation — precise location + destination PII to stdout and off-box via the loguru→Sentry bridge. Contradicts the geohash/phone-masking discipline enforced everywhere else. | Log `len(input)` / `bool(location)` only; never emit the raw query or coordinate string. One-line fix, high urgency. |
| C3 | **HIGH** | **Admin RBAC `role`/`modules` trusted from the JWT, not re-read from `admin_staff`.** The staff row is fetched (for `is_active`/`token_version`) but authorization uses the token's claims. | `dependencies/__init__.py:333-342`, `:620`, `:645` | A downgraded/module-revoked admin keeps elevated access for up to the 1h token TTL. Partial violation of the "authz from DB" rule the non-admin path correctly honors. | Build `role`/`modules` from the freshly-read staff row; bump `token_version` on any role/module change. |
| C4 | **HIGH** | **`OSRM_FALLBACK_URL` defaults to public `router.project-osrm.org`.** Light routing forwards coordinates to an uncontrolled third-party demo server with no DPA. | `core/config.py:131` | GPS egresses Canada by default, undermining the strict `ca-central-1` residency enforcement elsewhere. | Default the fallback to empty (opt-in) or a self-hosted Canadian OSRM; document the residency constraint. |
| C5 | **MED-HIGH** | **Corporate email-OTP has no brute-force lockout** (only IP rate-limit) while phone OTP has per-destination lockout. 4-digit codes = 10⁴ space. | `routes/auth.py:637-665` | Corporate accounts get materially weaker OTP protection than consumer phone accounts. | Mirror `_check_otp_lockout`/`_record_otp_failure` keyed on hashed email; move email OTP to 6 digits. |
| C6 | **MED** | **`confirm_payment` claims ride → `processing` then can raise without rollback**, and the idempotency guard then returns `already_processed` on retry → rider stranded on "processing" until an out-of-band sweeper recovers. | `routes/payments.py:391`, `:441/:460/:482`, guard `:366-373` | Rider cannot re-pay in-app after a genuine confirm failure. | Reset `payment_status` to prior value on failure (try/except around post-claim work), or exclude self-inflicted `processing` from the early return. Verify `stuck_ride_sweeper` coverage first — may lower severity. |
| C7 | **MED** | **Static 4-digit review-login bypass works in production** (`REVIEW_LOGIN_ACCOUNTS`). Intentional for store reviewers, but a stale entry is a static credential. | `routes/auth.py:327-341`, `config.py:176` | Anyone who learns the 4-digit code owns that allow-listed account; only bounded by 5-fail/24h lockout. | Per-entry hard expiry + alert when the var is non-empty in production; operational cleanup post-review. |

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**Strong foundation.** The backend's central handler (`utils/error_handling.py`) sanitizes every 5xx body to a vetted `ERR_*` sentinel and pairs it with a request-ID that matches the server logs — a genuinely production-grade "no stack traces to clients, full detail to admins" split. The shared client's token-refresh concurrency (dedup, subscriber queue, foreground/background rotation race handling, SOS exemption) is careful, real work. Sentry scrubbing (`sendDefaultPii:false`, GPS/raw-body stripping) is disciplined.

**Gaps:**
- **T1 · HIGH (UX):** The mobile `ErrorBoundary` renders `error.name: message` **and the full React component stack / `error.stack`** to end users in **production** (not just `__DEV__`) — a deliberate choice (`ErrorBoundary.tsx:36-40,47-55`) to diagnose from a TestFlight screenshot. On any render crash a real App-Store user sees `TypeError: undefined is not an object …` plus an element dump, and the message line can echo PII. **Fix:** gate diagnostics behind an internal-build/channel flag; ship only "Something went wrong" + Try Again to production, route the stack to Sentry.
- **T2 · MED (PII):** Client `fetch()` failure logs the full URL incl. query GPS on one path (`shared/api/client.ts:68-70`), bypassing the `_redactGpsUrl` helper used everywhere else. **Fix:** redact before logging.
- **T3 · MED:** Admin global error page renders `error.message` verbatim (`admin-dashboard/src/app/error.tsx:14`). Lower risk (admin-only) but same pattern — show generic + digest, log detail server-side.
- **T4 · INFO:** Dispatch pass/quota filters fail **open** on DB error and log at ERROR but emit **no metric** (`dispatch_service.py:402-410,423-431`), so a sustained outage silently disables subscription/quota enforcement without alerting. **Fix:** add a degradation metric like the presence-filter path already has.

---

## 🐢 Performance Bottlenecks & Optimizations

- **P1 · HIGH — Surge supply count is a full-fleet scan repeated per area.** `_count_supply_in_area` reads up to 5000 driver rows with **no service-area filter** and does point-in-polygon in Python, called once per area in the 2-min engine **and** again per area on the admin dashboard (`surge_engine.py:161-167,213,267,319-327`). N areas × full-fleet reads every 2 min. **Fix:** read the online fleet once, bucket by polygon in memory, or ship the already-written PostGIS RPC (`_count_supply_spatial`) as default; `asyncio.gather` the per-area reads and cache the last recalc for the dashboard.
- **P2 · MED — Surge cap-truncation writes a wrong regulated price instead of skipping.** On hitting the 5000-row fetch cap the code logs ERROR + a metric but **falls through and prices off the truncated count** (`surge_engine.py:95-101,168-174,266-303`). Truncated supply over-prices; truncated demand under-prices. **Fix:** on cap-hit, keep the last-good multiplier and skip the update until the spatial count lands.
- **P3 · MED — DB access is PostgREST-over-HTTP through a 64-thread executor, not a real connection pool.** Every query is an HTTP+JSON round-trip capped at thread-pool size; `psycopg2-binary` is already a dep but unused for the hot path. **This is the real scaling ceiling** — dispatch/geo will hit the thread wall before Postgres is stressed. **Fix:** move hot-path reads/writes (dispatch, ride state, driver location) to **asyncpg via a real async pool**, repository-by-repository behind the existing `repositories/` seam; keep `supabase-py` for admin CRUD.
- **P4 · LOW-MED — Per-dispatch `service_areas` re-reads** (2-3 reads of the same rows per dispatch on the <2s offer clock; `dispatch_service.py:270,366,369`). **Fix:** resolve area+parent once, pass through.
- **P5 · LOW — No edge caching for static config GETs.** Cloudflare already fronts the API; fare config / service-area polygons / vehicle types hit Python every request. **Fix:** `Cache-Control`/`ETag` + let Cloudflare cache at the edge. Near-zero effort.

*SLA note:* the critical paths (<2s dispatch, <300ms estimate, <1s settlement) are already defended — Stripe wrapped in `to_thread`, ETA `timeout=1.2s` with haversine fallback, receipts/push spawned off the settlement path. The surge engine is an internal loop, not a request path, so P1/P2 are cost/correctness, not latency-SLA, issues.

---

## 💡 Tech Stack & Architecture Recommendations

*Ranked by leverage. The monolith itself is the right call — Uber ran a monolith well past this scale; splitting now is pure ops tax.*

1. **Distributed tracing (OpenTelemetry) — highest leverage.** Today: per-process metrics + Sentry errors, three disconnected planes, no shared trace-ID. A slow book→dispatch→offer→accept flow crossing WS pub/sub, the DB thread pool, and Stripe is a black box. **Adopt:** OTel FastAPI+httpx auto-instrumentation → Sentry Performance (already paid for) or Grafana Tempo; propagate `trace_id` into Loguru + Sentry. Additive, days not weeks. *This is the #1 thing you'll wish you had in your first incident.*
2. **Feature-flag / kill-switch layer.** Only lever today is a global `app_settings` row with 60s TTL — no per-user/percentage rollout, no instant kill. **Adopt:** self-host Unleash/GrowthBook, or a `flags` table + Redis-cached evaluator reusing existing infra. Gate risky subsystems (surge, scheduled dispatch, promo, corporate billing) so a misbehaving path dies in seconds without a deploy. (Matches ACTION_ITEMS E5.)
3. **Real staging environment.** Deploys go `main` → prod (Fly+Railway) with only a shared "test" env doubling as QA. **Adopt:** promote a prod-shaped Fly staging on a separate `ca-central-1` Supabase with anonymized data; run the existing `loadtest/` harness against it before promotion. Prereq for load testing + safe migration rehearsal. (E1/E2.)
4. **Fail loud on Redis outage in production.** `redis_client.py` silently degrades to an in-process dict; in that state OTP lockout, rate limits, WS fan-out, and leader locks all go local-only/incoherent across replicas, and it doesn't page. **Adopt:** managed HA Redis (Upstash/Fly) + refuse boot / fire P1 when `REDIS_URL` is unreachable in prod. Removes a whole silent-failure class.
5. **Async DB pool (asyncpg)** — see P3. The single highest-impact perf/scaling move.
6. **Extract the ~30 background loops into a worker deployment** (`ROLE=worker`, no HTTP). Leader locks make every-replica-runs-everything *safe* but every pod still wakes, queries, and contends. Fine at 2-3 replicas, wasteful at 10. Same image, a launch flag + a second Fly process group; zero loop code change.
7. **Slim the serving image.** `requirements.txt` pulls OpenAI + Anthropic + Google GenAI + pandas/numpy + pyiceberg + boto3 into the request-serving image. Larger attack surface, slower cold start. **Adopt:** move AI/analytics deps to extras; keep the core API image lean.

**Explicitly DO NOT adopt yet** (over-engineering pre-launch): Kafka/SQS event bus (you have one consumer — reach for Redis Streams if/when a second appears), Temporal (consider *only* for the payment/payout saga if reconciliation drift recurs), microservices, multi-region. The reviewers were unanimous these are correctly deferred.

---

## 🛠️ Maintainability & Code Smells

- **Divergent dispatch twins.** `services/dispatch_service.py` has a `find_candidate_drivers` that **omits the mandatory geo bounding-box** its own module docstring warns about (`:329-333` vs the helper at `:75-102`), and an `assign_driver_to_ride` with **no pre-state guard** (`:442-454`) — while the live route in `rides/matching.py` implements both correctly. The service is a buggy, unused-in-prod twin that violates the CLAUDE.md ≥80% dispatch-coverage rule and is a landmine for the next caller. **Fix:** collapse to one correct implementation the route and service share.
- **Dead duplicate API client.** `rider-app/utils/apiClient.ts` is a second, weaker axios client (naive 401 refresh, no dedup, `localhost:8000` fallback) with zero call sites — a footgun that bypasses App Check/CSRF/redaction if wired up. **Fix:** delete.
- **Legacy `frontend/` app** leaks raw `error.response.data.detail` via `Alert.alert` (`frontend/app/login.tsx:63`) — appears superseded by rider-app/driver-app but still in the tree. **Fix:** confirm dead and remove.
- **Type-fragile comparisons on the money path:** surge change-detection compares a Python float to a DB-deserialized value (`surge_engine.py:272`) → write amplification + history bloat; Stripe idempotency detects unique-violations by **error-string matching** (`wallet_repo.py:281`) instead of PG code `23505`. Both work today but are one library-format change away from breaking. **Fix:** quantized Decimal compare; detect on error `code`.
- **Concurrent Stripe-customer creation** can mint duplicates (`payments.py:127-148`) — benign but accumulates orphans. **Fix:** compare-and-set write or unique constraint.

---

## 🧪 Testing & QA (missing edge cases)

**Posture is strong** — 348 test files, explicit unit/integration/e2e/slow tiers, E2E ride-lifecycle/cancellation/payment-guard/SOS/WAV suites, perf baselines, a mocked-Supabase fixture, and a coverage floor ratcheting 60%→80%. **Gaps to close, mapped to the findings above:**

- **The single biggest gap (ACTION_ITEMS A1):** global floor is 60% but CLAUDE.md mandates ≥90% on `payments.py`/`fare_service.py` and ≥80% on `rides.py`/`dispatch_service.py` — **not yet enforced per-path in CI.** Add `coverage report --fail-under` gates per money/dispatch path.
- **Missing regression tests for this review's confirmed bugs:** (a) minimum-fare ride where `charged == driver_earnings + admin_earnings` must hold (C1); (b) `confirm_payment` failure-then-retry does not strand the ride (C6); (c) surge cap-hit keeps last-good multiplier, never prices off truncation (P2); (d) `dispatch_service.find_candidate_drivers` applies geo bounds (dispatch twin).
- **Auth edge cases:** corporate email-OTP brute-force lockout (C5), admin role-downgrade-takes-effect-immediately (C3). Both are "allowed AND denied path" tests the RLS/auth convention already requires.
- **a11y in CI:** axe is in admin devDeps but not wired into E2E — WCAG 2.1 AA is a stated regulatory mandate (E11).

---

## Uber/Lyft benchmark — where Spinr stands

| Dimension | Uber/Lyft-class | Spinr today | Read |
|---|---|---|---|
| Money correctness | Penny-exact ledgers, receipt == payout | Decimal end-to-end, server-authoritative recompute — **but C1 breaks receipt==ledger on min-fare** | Ahead on rigor, one real bug to close |
| Dispatch | Dedicated matching service, geosharded | In-process asyncio, atomic claims, durable backstop | **Right-sized**; correct for the scale |
| Realtime | Bespoke pub/sub at massive fan-out | WS + Redis pub/sub, seq/replay outbox | **Right-sized**; done properly |
| Observability | Full tracing, SLO error budgets, synthetic probes | Metrics + Sentry, no tracing, no external probes | **Behind** — the real gap (recs #1, E4) |
| Rollout safety | Flags, %-rollout, kill switches, staging | Global `app_settings` only, no staging | **Behind** — recs #2/#3 |
| Data/privacy | Mature, region-locked | PIPEDA residency gate, retention, T4A, reconciliation — **strong**, minus C2 log leak | **Ahead** for the regulated-Canadian niche |
| CI/CD & supply chain | Extensive | 21 workflows, SAST, secret-scan, migration safety | **At or above** peer stage |

**Net:** Spinr is *ahead* of a typical pre-launch on correctness scaffolding and regulatory posture, and *behind* on operational observability/rollout-control — exactly the inversion you'd want at this stage, because the first class is expensive to retrofit and the second is additive.

---

## The Plan — prioritized

**P0 — before public launch (correctness/privacy/authz):**
1. C1 fare uplift attribution + reconciliation assertion + regression test.
2. C2 maps_proxy PII log redaction (one-line, do today).
3. C3 admin RBAC from DB row, not JWT claims.
4. C4 OSRM fallback default → opt-in / Canadian host.

**P1 — launch-hardening (auth + money resilience):**
5. C5 corporate email-OTP lockout + 6-digit codes.
6. C6 confirm_payment rollback-on-failure (verify sweeper first).
7. P2 surge cap-hit keep-last-good.
8. T1 gate mobile ErrorBoundary diagnostics to internal builds.
9. Per-path coverage gates in CI (A1); add the four regression tests above.

**P2 — operational depth (the launch-de-riskers):**
10. OpenTelemetry tracing + shared trace-ID across Sentry/Loguru (rec #1).
11. Feature-flag / kill-switch layer for risky subsystems (rec #2, E5).
12. Real staging env + run the existing loadtest harness (rec #3, E1/E2).
13. Fail-loud Redis in prod + managed HA Redis (rec #4).
14. P1 surge full-scan → single fleet read / PostGIS RPC.

**P3 — scaling & hygiene (post-launch, tracked):**
15. asyncpg pool on hot paths (rec #5, P3).
16. Collapse dispatch service/route twins; delete dead API client + legacy `frontend/`.
17. Worker-role deployment for background loops (rec #6).
18. Edge-cache static config GETs (P5); slim serving image (rec #7); external synthetic monitoring (E4).

**Do NOT do:** Kafka/SQS, Temporal (except possibly the payment saga later), microservices, multi-region. Premature at this stage — all four reviewers agreed.
