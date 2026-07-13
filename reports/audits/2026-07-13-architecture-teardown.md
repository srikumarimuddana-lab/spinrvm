# Spinr — Chief-Architect Teardown & Remediation Plan
**Date:** 2026-07-13 · **Mode:** Read-only review (no code changed) · **Reviewer:** Engineering-Director pass
**Scope:** backend (FastAPI/Python 3.12), rider/driver apps (Expo SDK 55, RN 0.85, React 19), admin (Next.js 16, React 19), shared TS lib.
**Method:** Four parallel domain audits (security/auth · payments/money · dispatch/WS/perf · error-handling/telemetry/QA/stack), each grounded in the actual source.

> **Headline:** This is an *unusually mature, defense-in-depth* codebase for a pre-launch product — closer to a Series-B platform than an MVP. No new **Critical** severity issues surfaced; the auth core, Stripe idempotency, Decimal discipline, and error-sanitization are genuinely strong. The remaining work is a short list of **High** correctness/scale items and a set of **stack-maturity** investments needed before Uber/Lyft-scale traffic. The plan below is prioritized, not exhaustive rewrites.

---

## 🚨 Critical Issues & Security Flaws

Nothing rises to *Critical*. The highest-impact correctness/security items:

| # | Sev | Finding | File | Why it matters |
|---|-----|---------|------|----------------|
| C1 | **High** | **Min-fare uplift is unallocated** — `total_fare = max(subtotal, minimum)` but `driver_earnings`/`admin_earnings` are computed from the *un-clamped* subtotal, so `(minimum − subtotal)` is collected from the rider yet credited to no one. The receipt's "Ride fare" line *includes* the uplift, so the receipt attributes money to the driver that the payout snapshot never credits. | `services/fare_service.py:210-213`, `:268` | Directly contradicts the **0% commission / "driver keeps 100%"** brand promise and the **"no hidden fee"** guardrail in CLAUDE.md — the platform silently keeps the delta and the receipt doesn't reconcile with the payout. Financial + reputational + regulatory (receipt transparency). |
| C2 | **High** | **Surge recalculation loop has no leader lock.** Every replica independently runs the full 2-min surge tick (scan online drivers per area → Python `point_in_polygon` → write `service_areas`). | `utils/surge_engine.py:356` | Violates the background-loop replay-safety recipe. Convergent (same value) so not a correctness bug, but it is N× regulated-price computation + write amplification on every replica — the one loop that mutates a *legally-capped price*. |
| C3 | **High** | **Re-dispatch re-scans the full 500-row driver pool on every retry** (up to ~30 attempts/ride, every 10–15s) during supply shortage. | `routes/rides/matching.py:1171` → `match_driver_to_ride` | `N_rides × 500-row reads` every 10s — a DB-load/latency amplifier that fires *exactly when the system is already stressed* (supply gap), threatening the <2s dispatch SLA under load. |
| C4 | **Med** | **Corporate master-wallet debit carries no idempotency key** — `apply_adjustment` is called without a ride/PI token; the RPC only dedups on `stripe_pi`. Replay-safety rests entirely on the single `processing`-claim guard. | `services/payment_service.py:428-438`, `services/corporate_wallet_service.py:45-55` | If `settle_corporate` is ever invoked outside that one claim, the master wallet double-debits real money. |
| C5 | **Med** | **CORS hardcodes `http://localhost:3000/3001` into the production allow-list** with `allow_credentials` enabled. | `core/middleware.py:571-598` | In prod, the API accepts *credentialed* cross-origin requests from any localhost process (malicious Electron app, compromised local dev server). Gate the localhost entries behind `if not is_production`. |

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**This is the strongest part of the codebase — hold the line here.**

- ✅ `utils/error_handling.py` enforces a strict *sentinel-or-sanitize* rule (`_should_sanitize_5xx_detail`): raw 5xx detail (Stripe charge IDs, Supabase constraint names, JWT lib errors) is replaced with a generic client message while full detail + `request_id` hits the server log. **No stack traces leak to end-users.**
- ✅ `DatabaseError` 5xx paths log at ERROR with `details['original']` (the real cause), honoring the "never swallow DB/auth/payment errors" rule.
- ✅ `utils/sentry_scrub.py` scrubs phone/email/GPS/postal in `before_send`/`before_breadcrumb`, lifts `domain/surface/ride_id` onto Sentry tags, and never drops an event.
- ✅ No truly bare `except:` in production; the ~414 `except Exception` blocks are narrowly scoped; critical-path `logger.warning` sites are legitimate "degraded-but-recovered" cases.

**Gaps:**

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| T1 | **Med** | **Documented metric `spinr_dispatch_presence_filter_failed_total` is never emitted** — `matching.py:294-296` only logs a warning. Any dashboard/alert on that series is silently dead. | Add `metric_inc(...)` at the two presence-degradation sites. |
| T2 | Low | Per-process metrics only (`utils/metrics.py`) — gauges reset on restart; short-lived background-loop metrics have no push-gateway, so loop failures may not surface in Prometheus. | Add a Prometheus push-gateway or OTel exporter for loop/gauge metrics (see stack section). |
| T3 | Low | `GET /admin/auth/session` trusts JWT claims with no revocation/`is_active` re-check; a force-logged-out admin still gets `authenticated:true` until `exp` (≤1h). Data endpoints re-verify, so this is UI-gating only. | Route through `_verify_admin_payload`. |

**Net:** raw errors do **not** leak to users; graceful degradation is real; admin-side logging has sufficient context. The only telemetry defect that matters is the dead metric (T1) creating a false sense of coverage.

---

## 🐢 Performance Bottlenecks & Optimizations

**Done well (do not regress):** geo bounding-box pre-filter before `LIMIT 500` (avoids "nearest driver in row 501"); column projection instead of `SELECT *` on the hot path (keeps encrypted PII off dispatch); ETA ranking bounded by `asyncio.wait_for(..., 1.2s)` with haversine fallback; presence/offer-skip/subscription checks batched via `MGET`/`$in` (no N+1); per-message WS send timeout (2s) so one half-closed socket can't stall fan-out; throttled location writes (3s) and admin fan-out (3s). No SLA-breaching inline blocking calls — Twilio/Stripe/push are consistently offloaded via `spawn`/`create_task`; blocking crypto pushed to a thread.

**Bottlenecks (beyond C2/C3 above):**

| # | Sev | Finding | File | Fix |
|---|-----|---------|------|-----|
| P1 | Med | `get_nearby_drivers` WS handler lacks the geo box — reads arbitrary `LIMIT 100` online drivers then haversine-filters in Python (same row-101 false-negative the dispatch path already fixed). | `routes/websocket.py:1101` | Apply `dispatch_geo_bounds`. |
| P2 | Med | Redundant `service_areas` reads on the dispatch hot path — `resolve_matching_config`, subscription, quota/timezone, and cascade each `find_one` the same row (~3–4 reads/attempt). | `dispatch_service.py:270`, `matching.py:320/416/450` | Fetch once, thread through. |
| P3 | Med | Batch-location WS handler bypasses the `resolve_active_rides_cached` (5s) cache — issues a fresh `get_rows("rides", …)` per batch. | `websocket.py:978` | Use the cached resolver. |
| P4 | Low | Batch rate-limiter loops `redis_incr` up to ~500×/message. | `websocket.py:910` | Single `INCRBY`. |
| P5 | Low | WS pub/sub uses one shared channel — every replica JSON-parses every unicast to check locality (O(replicas × msg-rate) bandwidth). | `utils/ws_pubsub.py:49` | Pattern-subscribe / per-node channels as socket count grows. |

---

## 💡 Tech Stack & Architecture Recommendations

The current stack is modern and pinned (FastAPI 0.136, pydantic 2, Next 16, React 19, Expo 55, stripe 15) with a proper half-open circuit breaker + row-cache in `repositories/_base.py` and 21 CI workflows. The gaps are the ones that separate "scales to launch" from "scales like Uber/Lyft":

1. **No async DB driver; PostgREST-over-HTTP wrapped in a threadpool.** All DB access is synchronous `supabase-py` on `run_sync`. The threadpool (`DB_THREAD_POOL_SIZE`, 32) is a hard concurrency ceiling and PostgREST adds a hop of latency versus a native pool. **→ Introduce `asyncpg`/psycopg3 + PgBouncer for the hot paths (dispatch, fare settlement, location writes)** while keeping supabase-py for CRUD. This is the single highest-leverage scale investment.
2. **No durable task broker.** 16 in-process asyncio loops poll on *every* replica, relying on DB atomic-claims / Redis locks (clever, but a thundering-herd pattern), and external calls go inline or via `create_task` — at risk of loss on crash. **→ Add a durable queue (Redis Streams / NATS / SQS) with a DLQ** for Twilio/Stripe/push and for the polling loops; gives real retry semantics and decouples fan-out.
3. **Redis fallback silently weakens security in prod.** `utils/redis_client.py` falls back to a non-thread-safe in-process dict when `REDIS_URL` is unset — rate-limit and OTP-lockout state then live per-replica and vanish on restart. **→ Hard-fail startup in production when `REDIS_URL` is unset** (you already fail-fast on weak JWT secret; extend the pattern).
4. **Geospatial indexing.** You bounding-box + haversine in Python. Uber indexes supply with **H3**; Lyft uses **S2**. **→ Adopt PostGIS `GIST` indexes or an H3 cell column on drivers** so supply lookup is index-only and re-dispatch (C3) doesn't re-scan.
5. **Dispatch as a monolith router.** Fine for launch, but dispatch is the latency-critical, independently-scaling core. **→ Plan to extract dispatch into its own service** with its own pool once traffic justifies it (this is exactly the Uber/Lyft split: a dedicated matching service behind the API).
6. **Observability.** Per-process Prometheus, no distributed tracing. **→ Add OpenTelemetry tracing** (request → dispatch → DB → Stripe spans) and a managed Prom/Grafana + push-gateway. You already have Sentry + structured logs; tracing is the missing third pillar.

**vs. Uber/Lyft, honestly:** Spinr's *correctness* discipline (Decimal money, idempotency, error sanitization, fail-closed auth, RLS, PIPEDA/SGI compliance baked in) is at or above what many scaled players ship. What it lacks is the *horizontal-scale plumbing* — async DB, a real broker, geospatial indexes, service decomposition, and distributed tracing — none of which are launch-blockers, all of which are the next 6-month arc.

---

## 🛠️ Maintainability & Code Smells

- **Dead legacy receipt hardcodes GST 5% + PST 6%** (`utils/receipt_email.py:18-19,55-56,86-87`) — contradicts the authoritative SK policy (GST-only, PST off by default) and its rows would exceed the DB `grand_total`. Only imported by tests today; **delete or fix before anything wires it up.**
- **Stuck-corporate rides under-recovered** — a crash after allowance+master debit but before the `paid` write leaves the ride in `processing` forever; `process_payment` then reports `already_paid` and the reconciler is detection-only for non-card paths. No double-charge, but real money can sit debited until manual intervention. **→ Extend `stripe_reconcile.py` to sweep stuck `processing` corporate rides.**
- **Migration prefix collisions** (08, 28, …, 58) are pre-existing and handled by full-filename keying, but they remain a footgun — the 2026-04-28 slot-56 incident (two PRs `CREATE OR REPLACE`-ing the same function off different forks, silently regressing retention) is documented in the sprint log. The cross-PR migration-target CI check is the right permanent fix; keep it enforced.
- Redundant `service_areas` reads (P2) are also a readability smell — the same row fetched 3–4× per request signals a missing request-scoped context object.

Overall the code reads cleanly, comments are dense where they matter (money, state machine, insurance periods), and the dual-import + Decimal-helper conventions are consistently applied.

---

## 🧪 Testing & QA (Missing Edge Cases)

**Strong baseline:** 313 backend test files; 6 E2E suites (ride lifecycle, cancellation, payment-guard, SOS, WAV dispatch, rating); a dedicated 321-line `test_ride_state_machine.py`; ~35 corporate-billing tests; dispatch has DB-error/metrics/perf suites; admin has Vitest + Playwright; rider/driver have 58 test files.

**Gaps:**

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| Q1 | **Med-High** | **Per-domain coverage floors are not enforced.** `pytest.ini` sets a single global `--cov-fail-under=60`, but CLAUDE.md mandates payments/fare/crypto ≥90% and rides/dispatch ≥80%. A money-path regression to <90% stays green as long as the aggregate holds. | Add per-module `fail_under` (coverage contexts or a CI post-step asserting per-file thresholds). |
| Q2 | Med | **No regression test for C1 (min-fare allocation)** or C4 (master-wallet idempotency) — both are money-invariants with no guard. | Add `test_min_fare_allocation_reconciles_payout` and a double-debit replay test. |
| Q3 | Low | Confirm `test_ride_state_machine.py` asserts the *forbidden* transition (`cancelled` after `in_progress` rejected), not just happy paths. | Add the negative case if absent. |

---

## 📈 Manager's Verdict (Overall code health)

**Grade: B+ / A-.** This is a well-architected, compliance-first platform with a level of correctness discipline (money, auth, idempotency, error hygiene, PIPEDA/SGI) that is rare pre-launch and materially ahead of a typical Uber-clone. **It is safe to launch on the current architecture.** The risk profile is not "is it correct?" but "will it scale and does the money reconcile?" — and there the list is short and concrete.

**Prioritized plan (do in this order):**

1. **Now (correctness / brand-integrity, this sprint):** C1 min-fare allocation (money doesn't reconcile with a core brand promise) + Q2 regression test; C5 CORS localhost gate; T1 dead metric.
2. **Next (scale-safety, before real load):** C2 surge leader-lock; C3/P1–P3 dispatch re-scan + geo-box + cached-context reads; C4 master-wallet idempotency key; #3 Redis hard-fail-in-prod; Q1 per-domain coverage gates.
3. **Quarter (Uber/Lyft-scale plumbing):** async DB driver + PgBouncer on hot paths; durable task broker + DLQ; H3/PostGIS supply index; OpenTelemetry tracing; dispatch-service extraction plan.

None of tier 1–2 is a large refactor; each is a scoped, single-logical-change ticket. Tier 3 is the deliberate 6-month scale arc.

*— End of teardown. No code was modified; this is a plan, not a patch.*
