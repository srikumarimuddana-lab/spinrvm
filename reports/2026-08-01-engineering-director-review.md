# Spinr — Engineering-Director Code Review & Action Plan
**Date:** 2026-08-01 · **Scope:** backend (FastAPI/Supabase/Redis), with frontend error-handling spot-checks · **Mode:** read-only teardown, grounded in current `main`/`claude/epic-planck-ccyh3i` code · **Benchmark:** Uber / Lyft class platform

> **Bottom line up front.** This is a **mature, heavily-defended codebase** that has clearly survived multiple prior hardening rounds — comments cite specific audit IDs (SEC-008, C2–C7, B-P1/2/3, R-P1). The core trust-model, money-math, Stripe idempotency, and dispatch-race primitives are implemented correctly and match the documented conventions. The findings below are **residual gaps, not a system in trouble.** Two are true pre-merge blockers; the rest are latency, telemetry-maturity, and industry-parity work. Health grade: **B+ / "production-capable, pre-scale."**

---

## 🚨 Critical Issues & Security Flaws

| # | Severity | Finding | Location | Why it matters (root cause) | How to fix (conceptual) |
|---|---|---|---|---|---|
| C1 | **BLOCKER** | Driver can **accept an offer after going offline mid-offer-window** | `routes/drivers/status.py:183-208` + `routes/drivers/ride_flow.py:210-233` | Go-offline only refuses the toggle for `driver_accepted/arrived/in_progress`. It does **not** block `driver_assigned` (the ~15s pending-offer window). `accept_ride`'s atomic claim filters on ride state + `driver_id` only — it never re-reads `driver.is_online`. Net: a driver taps "offline" during the offer, then still accepts, entering a trip while flagged offline → **insurance-period + dispatch-integrity violation.** `domain-dispatch.md:32` documents this exact mitigation as *required* but it isn't in code. | Re-read `driver.is_online` fresh immediately before the atomic ride UPDATE in `accept_ride`; 409 + release the offer if false. |
| C2 | **BLOCKER (money/receipt)** | Rider receipt **omits the discount/promo line**, so itemized rows don't sum to the charged total | `utils/email_receipt.py:72-200` (`_build_fare_rows`) | Rides carry `discount_amount`/`promo_code` and `grand_total` is already netted of the discount, but the email builder renders no negative "Discount" row. Rows visibly under-sum vs the total → violates the "every charge maps to a disclosed line item" transparency rule and is a **PIPEDA / consumer-transparency** exposure. | Add a negative "Discount" row sourced from `ride.discount_amount`/`promo_code`, mirroring the existing `tax_breakdown` loop. |
| C3 | HIGH (security-degradation) | **OTP-lockout & rate-limit state lost on restart** in Redis-less mode; prod still boots | `core/lifespan.py:116-123`, `utils/redis_client.py` | Prod without `REDIS_URL` logs an error but boots anyway. OTP brute-force lockout + per-user rate counters live in an in-process dict → reset every deploy, and are per-replica not fleet-wide. Silent security downgrade. | Make Redis a **hard fail-fast boot requirement in production** (same posture as the Supabase health check), not a warning-and-continue. |

> **What's already correct (verified, no action):** admin JWT `aud`-binding + JTI revocation + staff-active + idle-timeout; rider/driver role always re-read from DB (never trust JWT role claim); OTP SHA-256/HMAC at rest with 5-fail→24h lockout; dev-bypass `1234` hard-refused in prod; Stripe webhook idempotency via `claim_stripe_event` with `unclaim`+`logger.critical` on transient failure; RLS `FOR ALL` grants superseded by enumerated policies (migrations 142/262); CORS wildcard fail-fast; CSRF double-submit; SOS never gated on fresh JWT; `log_guard.py` sink-level PII redaction by key **and** value pattern.

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**User-facing side is genuinely strong.** No raw error / stack trace / DB detail reaches riders or drivers: 5xx `detail` is centrally sanitized to `ERR_*` sentinels, Stripe/DB exceptions map to generic client-safe copy ("Payment provider error. Please try again."), and the frontend has `ErrorBoundary` coverage on the rider ride-flow screens with no raw-error leakage into toasts. This is above the bar most pre-launch products clear.

**The gaps are on the admin-observability side:**

| # | Severity | Finding | Location | Why / How |
|---|---|---|---|---|
| E1 | WARNING | **Payment failure logged at `warning`, so it never reaches Sentry** | `routes/webhooks.py:790` | `payment_intent.payment_failed` → `logger.warning(...)`. By the observability convention, `warning` = "degraded-but-recovered, never Sentry." A real card decline is invisible to on-call dashboards. → `logger.error(..., extra={"domain":"payments","event_id":...,"ride_id":...})`. |
| E2 | WARNING | **Raw exception interpolated into `HTTPException(detail=...)`** at the raise site | `documents.py:365, 999, 1013` (`detail=f"...: {e}"`) | Safe *today only by accident* — the central 5xx sanitizer strips it. But it's the exact anti-pattern `error_handling.py` exists to close, and it's fragile if handler precedence (Starlette MRO) ever shifts. → Stop building client `detail` from `str(e)`; use `ERR_*` sentinels like `routes/auth.py` already does. |
| E3 | LOW | Admin LMS 502 may embed a driver phone in exception text | `routes/admin/drivers.py:2072` | Same accidental-safety pattern; also hits the server log unredacted. → Confirm `LMSUpstreamError` doesn't carry PII before logging/raising. |
| E4 | MED | **Redis warn-and-continue is spread across many call sites** | `socket_manager.py:168-172,533-536`, `_base.py:465-498` | Each is individually a legitimate fail-open, but a full Redis outage degrades rate-limiting + WS fanout + OTP lockout **simultaneously** with no single alarm. → Add one Redis-health gauge + alert so aggregate degradation is one loud signal. |

---

## 🐢 Performance Bottlenecks & Optimizations

| # | Severity | Bottleneck | Location | Impact vs SLA | Fix |
|---|---|---|---|---|---|
| P1 | **HIGH** | **Synchronous supabase-py behind a fixed 64-thread pool** → hard DB-concurrency ceiling per replica | `repositories/_base.py:161-162,238-295` (`ThreadPoolExecutor(max_workers=64)`) | The 65th concurrent coroutine queues behind the pool. Under a dispatch burst this directly threatens the **<2s dispatch P95**. Thread-per-query is a scaling anti-pattern. | Migrate hot read/write paths to **asyncpg** (native async, real pool, no thread hop); keep supabase-py for admin/low-QPS only. |
| P2 | HIGH | **PostgREST client pinned to HTTP/1.1** — no connection multiplexing | `supabase_client.py:30-45` (`http2=False`) | One in-flight request per connection → P1's 64 threads become ~64 connections with TLS/keepalive churn. Done to dodge h2 `GOAWAY` crashes — treating the symptom. | Front PostgREST with **PgBouncer**, or bypass it via asyncpg on hot paths. |
| P3 | HIGH (dispatch SLA) | **5 sequential uncached `service_areas` round-trips per dispatch attempt** for the *same* row | `dispatch_service.py:284-288`, `rides/matching.py:381-389,511-519`, `utils/spinr_pass.py:284-286` | Each filter block re-queries the same service-area row, 3 of 4 strictly serial — pure latency tax before an offer is even sent. | Fetch the service area (+parent) **once** at the top of `_match_driver_to_ride_attempt` and thread it through. |
| P4 | MED (dispatch SLA) | **Per-driver `quest_progress` lookup serializes the offer-notify loop** | `rides/matching.py:795-834` | Driver #2..#N's WS offer waits on driver #1's awaited round-trip. The one hot-path spot not using the `.in_()` batch convention already used for incentives. | Batch quest progress in one `.in_()` inside the existing `asyncio.gather`. |
| P5 | MED | **~30 background loops run on every replica; watchdog covers only 18** | `core/lifespan.py:203-566` vs `_WATCHDOG_LOOP_NAMES:522-543` | ~12 loops (`preauth_capture`, `route_finalizer`, `distance_reconciliation`, …) have **no staleness alerting**, and every loop competes for the same 64-thread pool on every replica. | Derive the watchdog list from the spawn registry programmatically; consider a **leader-only worker process** (arq / Celery beat) instead of N replicas × 30 loops. |
| P6 | MED | Metrics are **per-process, never aggregated** → fleet-wide P95 uncomputable | `utils/metrics.py:10-25,129-175` | Home-grown in-memory shim; the KPI SLAs (dispatch P95, fanout P95) can't actually be measured across the fleet from these. | Adopt **prometheus_client (multiprocess)** or push OTLP histograms so buckets aggregate correctly. |
| P7 | LOW | Admin driver-location fan-out has **no viewport filtering** | `socket_manager.py:410-425` | Throttled to 1/3s/driver but still N-drivers × A-admins. Fine now, won't scale. | Viewport/bbox subscription protocol before scaling driver count. |

---

## 💡 Tech Stack & Architecture Recommendations

| # | Gap vs Uber/Lyft-class platform | Evidence | Why it's a problem | Recommended tooling |
|---|---|---|---|---|
| T1 | **No message queue / event bus** — Twilio & Stripe are awaited *inline* in request handlers | `routes/auth.py:446`, `routes/payments.py:228,1035`, `routes/wallet.py:206`; no Celery/RQ/arq/Kafka in `requirements.txt` | `to_thread` keeps the loop free but the handler still blocks on the third-party round-trip and holds a worker slot → a Twilio/Stripe latency spike stalls **login and settlement**. | **Outbox pattern + async worker (arq or Celery-on-Redis)** for OTP/SMS/receipts/webhook side-effects; ack the client immediately, process out-of-band with retries. |
| T2 | **No staging environment** → the two-sided load test can't run | `ACTION_ITEMS.md E1`; `loadtest/locustfile.py` targets staging & asserts SLA gates, but E2 is "blocked on E1" | The **only** thing that validates dispatch/fanout SLAs under real concurrency effectively never runs. SLAs are currently unmeasured under load. | Stand up a staging Fly app + throwaway Supabase project; wire the Locust SLA gates into a nightly job. |
| T3 | **No real feature-flag / kill-switch framework** beyond ad-hoc `app_settings` rows | `ACTION_ITEMS.md E5` | No percentage rollout, segment targeting, or audited flip history for risky subsystems (surge, dispatch, corporate billing) — yet the release gates *require* flagged rollout for shared components. | **Unleash** (self-hostable, Canadian-residency friendly) or LaunchDarkly. |
| T4 | **No distributed tracing** | `ACTION_ITEMS.md D2` — only `X-Request-ID` | Debugging a P95 breach across the `run_sync` thread boundary is blind. | **OpenTelemetry** auto-instrumentation (FastAPI + httpx + redis) → OTLP collector; manual context propagation across the thread hop. |
| T5 | **DR posture degraded** — Railway standby silently drifting from `main` | `ACTION_ITEMS.md C5` + CLAUDE.md deploy note | A Fly outage today fails over (single Cloudflare CNAME) to a **stale** build. | Unblock the Railway `deploy-backend.yml` env rule, or explicitly demote to cold standby with a documented rebuild step. |
| T6 | **Dispatch is greedy per-ride, not batched-wave matching** (structural, not a defect) | `rides/matching.py` whole flow | Uber/Lyft batch riders into short windows and solve a bipartite assignment to minimise aggregate wait/ETA. Fine at SK density; the single biggest structural difference at scale. | Revisit only if match-rate/utilization KPIs dip in dense markets. |
| T7 | **Point-in-time ETA + static destination-mode**, not continuous/predictive | `rides/matching.py:656-678`, `dispatch_service.py:136-167` | Single Distance-Matrix call at dispatch; destination filter is driver-toggled, not ML-predicted (Uber learns end-of-shift routes). Acceptable v1; the gap if driver-retention work targets this. | Live per-driver ETA + predictive destination filter — a roadmap item, not a fix. |

---

## 🛠️ Maintainability & Code Smells

- **`driver_earnings` reconstructed by subtraction** (`fare_service.py:212-221,375-379`): `total − admin_earnings` rather than the literal `base+distance+time`. Intentional (0% commission, minimum-fare uplift goes to the driver) and safe, but it's the reconstruct-by-subtraction pattern the money rules flag, and `domain-payments.md`'s literal formula doesn't document the exception → **doc and code silently disagree.** Fix the doc + add a cross-referencing comment.
- **Float in the surge engine** (`surge_engine.py:74-79,205-223,271-303`): tier math uses native `float` and writes straight to the numeric column. Today's tiers (1.0…2.5) are binary-exact so no drift *yet*, but this is outside the fare-code float pre-commit hook and a future `1.3×` tier would silently drift. Wrap in `Decimal`, quantize before write.
- **`/wallet/pay` guard checks `total_fare`, not `grand_total`** (`routes/wallet.py:251-256`): not a money-loss bug (the `wallet_pay_for_ride` RPC re-derives `grand_total` under a row lock), but a client obeying the route's own bound can still eat a confusing `ERR_FARE_UNDERPAID` on any ride with tax/fees. Align the pre-check to `grand_total`.
- **Watchdog registry drift** (P5): a hand-maintained loop list that has already fallen out of sync with the spawn list is a maintainability smell in its own right — make it derived, not duplicated.
- **CLAUDE.md says "17 background loops"; code spawns ~30.** Documentation drift on a safety-relevant subsystem.

---

## 🧪 Testing & QA (Missing Edge Cases)

| # | Severity | Gap | Evidence | Fix |
|---|---|---|---|---|
| Q1 | HIGH | **Corporate (money-moving) coverage ~52%**, well under its 80% tier; no module gate | CLAUDE.md + `pytest.ini:15` (`--cov-fail-under=60` global only); `corporate_accounts.py` ~39%, signup/rider/kyb ~32-33% | Add a **per-module `--cov-fail-under`** for `routes/corporate_*` / `services/corporate_*` and backfill the low files (untested branches, not absent files). |
| Q2 | MED | **All ~30 background loops skipped under a real lifespan in tests** → zero integration coverage of spawn/restart/leader-lock/watchdog wiring | `core/lifespan.py:176` (`_skip_background_loops` when `ENV==test`) | One dedicated integration test that boots the lifespan with loops enabled against `mock_supabase_client` and asserts spawn/cancel + watchdog registration. |
| Q3 | MED | **SLA load test exists but is unrunnable** (blocked on staging) | `loadtest/locustfile.py` asserts fare-estimate P95 <300ms & accept P95 <2s at `test_stop` | Couple with T2 — nightly Locust run against staging with the SLA gates as pass/fail. |
| Q4 | — | **Add regression tests for C1 and C2** as they're fixed | — | State-machine test: offline-mid-`driver_assigned` → accept must 409. Receipt test: discounted ride → rows sum to `grand_total`. |

---

## 📈 Manager's Verdict — Overall Code Health

**Grade: B+ — production-capable, pre-scale. Ship after the two blockers; fund the scaling backlog before real volume.**

- **Correctness & security (A-):** The hard stuff is right. Trust model, money math, Stripe idempotency, dispatch races, RLS, PII redaction, and user-facing error hygiene are all implemented to a standard above typical pre-launch. The residual security items are degradation modes (C3/E1/E4), not open holes.
- **Performance & scale (B-):** The synchronous-DB + HTTP/1.1 combination (P1/P2) is a real ceiling that will bite under a dispatch burst, and the dispatch hot path carries avoidable serial round-trips (P3/P4) that eat directly into the flagship <2s SLA. None are firefights today; all are foreseeable at 10×.
- **Operability (B):** Telemetry is present and thoughtfully PII-safe, but the home-grown metrics shim can't actually compute the fleet-wide KPIs the org measures itself against (P6), there's no tracing (T4), no staging to validate SLAs (T2), and the DR standby is quietly stale (T5). These are "you'll wish you had it during the first incident" gaps.
- **Maintainability (A-):** Code is well-commented with audit lineage, conventions are documented and largely followed. The smells are doc/code drift, not structural rot.

**The honest one-liner for leadership:** *the product logic is enterprise-grade; the platform underneath it is still single-region, thread-pool-bound, and under-instrumented for scale. The gap to Uber/Lyft is not correctness — it's an async data layer, an async job queue, real observability, and a staging pipeline to prove the SLAs.*

---

## ✅ Prioritized Action Plan

**Now — pre-merge blockers (this sprint):**
1. **C1** — gate `accept_ride` on a fresh `is_online` re-read (+ state-machine regression test). *Insurance/dispatch integrity.*
2. **C2** — add the negative discount line to the email receipt (+ receipt sum test). *Transparency/PIPEDA.*
3. **C3** — make Redis a hard prod boot requirement. *Security degradation.*

**Next — high-leverage, low-risk (next 1–2 sprints):**
4. **E1** — payment-failure `warning`→`error` with domain tags (5-line change, restores Sentry visibility).
5. **P3 + P4** — de-duplicate the service-area fetch and batch `quest_progress`; directly buys back dispatch-latency headroom.
6. **P6/M2** — swap the metrics shim for real `prometheus_client`/OTLP so the KPI dashboards are trustworthy.
7. **E2/E3** — stop `str(e)` in `HTTPException.detail`; audit the LMS 502 for PII.

**Then — platform investments (quarter):**
8. **T1** — async job queue (arq) for Stripe/Twilio/receipts side-effects.
9. **T2/Q3** — staging env + nightly Locust SLA gate (unblocks the only under-load validation).
10. **P1/P2** — pilot **asyncpg** on the dispatch hot path; front PostgREST with PgBouncer.
11. **T3** — real feature-flag/kill-switch framework (Unleash).
12. **T4/T5** — OpenTelemetry tracing; unblock or formally demote the Railway standby.

**Backlog / roadmap (parity, not defects):** T6 batched-wave matching, T7 predictive destination-mode + live ETA, Q1/Q2 corporate coverage gate + lifespan integration test, the doc/code reconciliations (surge Decimal, `driver_earnings` formula, "17 vs 30 loops").

---
*Read-only review. No code was modified. Findings verified against current source; the two blockers (C1, C2) were spot-checked at the cited line ranges.*
