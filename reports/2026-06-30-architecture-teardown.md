# Spinr — Engineering Director's Architecture Teardown

_Read-only review. 2026-06-30. Surfaces covered: backend core (dispatch/fare/payments/rides), backend infra/telemetry/data layer, frontend (rider/driver/admin/shared), DevOps/CI-CD/testing/security tooling. Benchmarked against mature Uber/Lyft-grade design._

> **Framing.** Spinr is a genuinely well-run codebase for its stage: 6 P0s closed, a disciplined append-only migration regime, money-as-`Decimal` enforced by pre-commit, MFA on staff, RLS-first tables, a documented SLA/KPI table, and an unusually honest `ACTION_ITEMS.md`. This teardown is therefore not "your code is bad" — it is "here is the delta between a solid regional-beta platform and a multi-region, scale-ready one." Line references come from automated deep-dives and should be spot-verified before a fix lands.

---

## 🚨 Critical Issues & Security Flaws

| # | Finding | File:line | Why it matters |
|---|---|---|---|
| C1 | **Potential double-charge under crash+retry.** Stripe idempotency keys are ride-scoped and embed `amount`/`payment_method_id` rather than a single immutable per-attempt token. If an attempt fails *after* Stripe dedupes but *before* the DB write, retry 2+ can re-charge. | `utils/payment_retry.py:340`, `utils/stripe_charge.py:174` | Real money. A double-charge in a regulated market is a P0 trust + chargeback event. |
| C2 | **Ride wedged in `processing`.** `charge_ride` returns `status="failed"` on a null payment method, but the ride is already flipped to `payment_status='processing'` and the caller never reads the outcome. | `utils/stripe_charge.py:156-160` | Rider's ride is stuck; only manual support unblocks it. Silent state-machine dead-end. |
| C3 | **Subscription/pass gate fails *open*.** A Supabase error on the `driver_subscriptions` lookup is logged and execution continues with *all* drivers eligible. | `services/dispatch_service.py:387-394` | An access-control gate silently disables itself on a transient DB blip — drivers without a valid Spinr Pass get dispatched. Compliance + revenue leak. |
| C4 | **Audit writes swallow errors, never retried.** `log_admin_action` try/excepts the insert, logs ERROR, returns `None` — the mutation (e.g. a $100 credit) still commits. | `utils/audit_logger.py:68-77` | Creates an *unfixable* audit gap (SOC 2 CC6.1). Critical mutations should fail-fast so the mutation rolls back with its audit row. |
| C5 | **Silent dispatch failure on driver-status race.** `claim_driver_atomic` succeeds, then a re-`get_driver_by_id` can see a concurrent suspension and silently reject the whole batch. | `routes/rides.py:1062` | Rider waits indefinitely while eligible drivers exist — directly hits the ≥85% match-rate KPI. |
| C6 | **Firebase App Check has no revocation.** Tokens are verified but valid up to their 1-hour TTL even after the app build is revoked. | `core/middleware.py:91-153` | A token lifted from a decompiled APK calls the API for up to an hour post-revocation. App Check becomes theater without short TTL + revocation list. |
| C7 | **Surge silently mis-priced on truncation.** When the demand/supply fetch hits the 5000-row cap, the count truncates; surge multiplier is computed on undercounted supply. Visible to ops only — the rider is charged the wrong number. | `utils/surge_engine.py:93-99`, `248-263` (airport sub-areas skip auto-surge with no fare-path equivalent) | In a province with a 2.5× regulatory cap and "surge must be visible before booking," a mispriced surge is a regulatory/fairness exposure, not just a bug. |
| C8 | **Raw error strings leak to end users (mobile).** `error.message` is shown directly in toasts/alerts on login, OTP, and payment-confirm screens. | `driver-app/app/login.tsx:110`, `rider-app/app/login.tsx:53`, `rider-app/app/otp.tsx:144`, `rider-app/app/payment-confirm.tsx:103` | "ECONNREFUSED" / backend exception text reaching a rider is a UX + information-disclosure smell. |
| C9 | **Server exception text rendered in Admin UI.** Next.js error boundaries print `error.message` to the DOM. | `admin-dashboard/src/app/error.tsx:14`, `dashboard/error.tsx:30` | Leaks code structure/stack context to anyone who can screenshot an admin error state. |

**Already tracked / partially mitigated:** disputes RLS-too-broad + full-name leak + floored refund cents (`routes/disputes.py:188,227`, ACTION_ITEMS B2 — still open). WS per-user rate limit is per-replica only (B4 — open). These are correctly logged; this review re-confirms they remain open.

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**The good:** the project has internalized the "don't silently swallow DB/auth/payment errors" rule — there's been a deliberate `logger.warning → logger.error(exc_info=True)` sweep, a loguru→Sentry bridge, `send_default_pii=False`, and a Stripe↔DB reconciliation cron. This is ahead of most startups.

**The gaps:**

- **User-facing leakage (P1).** Items C8/C9 above. There is no single translation layer that maps internal errors → safe, localized user copy. Uber/Lyft route *every* surfaced error through an i18n/error-code map; here each `catch (error: any)` decides for itself, and several pick `error.message`.
- **`catch (error: any)` everywhere (P1).** `payment-confirm.tsx:172`, `manage-cards.tsx:106`, `driver/payout.tsx:237`, etc. `any` defeats the type system: when the error shape changes, the handler crashes with "Cannot read property 'response' of undefined" *inside the catch block*. Narrow to `unknown` + a typed `isApiError()` guard.
- **429 handled asymmetrically (P2).** The shared client builds a proper `RateLimitError` with `retryAfterSeconds` (`shared/api/client.ts:776`), but rider screens catch it as a generic error — no "try again in 120s" countdown, so the user immediately re-triggers the lockout.
- **Client errors never reach observability (P2).** `recordApiError()` writes to an in-memory buffer + AsyncStorage but never calls `captureException` (`shared/api/client.ts:760`). A 402→retry→503→abandon cascade is invisible to support. No client↔backend `request_id` correlation.
- **Silent `.catch(() => {})` on the fare-estimate path (P2).** `rider-app/app/ride-options.tsx:245` swallows estimate failures → user taps "Book" on stale/empty fares → 402 surprise. No retry banner, no soft-fail UI.
- **Offline queue drops 4xx silently (P2).** `shared/api/offlineQueue.ts:164` drops failed jobs; if `setQueueErrorCallback` was never wired, a failed tip just vanishes and the rider believes it succeeded.
- **Backend telemetry is per-process (P1 for scale).** Metrics counters/gauges live in per-replica dicts (`utils/metrics.py:38`). A Prometheus scrape hits whichever pod the LB picks — a fleet that is 1-healthy/2-failed can report "circuit closed." Circuit state, thread-pool saturation, and cache-hit ratios are all invisible at fleet scale. **This is the single biggest telemetry gap.**
- **Pass-gate / retry-loop failures logged without metrics (P2).** `dispatch_service.py:370`, `payment_retry.py:391` log ERROR but emit no counter, so there's nothing to alert on.

**Net:** admin-side logging is strong; **user-facing graceful degradation and fleet-level metric aggregation are the two weak axes.**

---

## 🐢 Performance Bottlenecks & Optimizations

1. **N+1 driver re-fetch inside the dispatch hot path** (`routes/rides.py:1062-1073`) — per-claimed-driver `get_driver_by_id` = up to 10 serial round-trips on the <2 s offer SLA. Batch via `.in_()`.
2. **Inline Google Maps `batch_get_etas` awaited in dispatch** (`routes/rides.py:1043`) — a slow Maps call stalls dispatch up to the 3 s timeout before the haversine fallback. Pre-compute/parallelize, or fall back immediately and refine async.
3. **No DB connection pool — only thread-pool backpressure** (`repositories/_base.py:136`). 64-thread `ThreadPoolExecutor`; each Supabase HTTP call holds a thread to completion. The 65th request blocks. Thread starvation has no bulkhead — a batch export can starve auth/dispatch. Uber/Lyft pool *connections*, not threads.
4. **Surge engine is O(drivers × areas)** (`utils/surge_engine.py:150-194`) — Python point-in-polygon over up to 5000 drivers per tick. The PostGIS RPC exists but is env-gated with no auto-detect (ACTION_ITEMS D1). Past ~1000 online drivers this degrades and the cap silently distorts pricing (see C7).
5. **Broadcast fan-out is serial with a 2 s per-socket timeout** (`socket_manager.py:315-336`) — 110 sockets worst-case ≈ 220 s for one broadcast; one stalled client delays everyone. Bound concurrency with a semaphore + `asyncio.gather`.
6. **`broadcast_to_admins` ships every driver update to every admin** (`socket_manager.py:338-355`) — 500 drivers × 50 admins ≈ 25k msgs/s, filtered client-side. Needs server-side viewport/geo filtering before enterprise-fleet scale.
7. **No read caching layer for hot reads** — fares cache exists (`routes/fares.py`) but admin analytics, vehicle types, app_settings, and surge state hit Supabase every request. ACTION_ITEMS D7 (5-min analytics cache) alone is cited as ~98% DB-load reduction.
8. **`get_rows()` has no default `limit`/pagination** (`repositories/_base.py:311`) — a wide query loads the whole table into memory → OOM risk. Force a default cap + cursor.
9. **Payment-retry loop: fixed 300 s interval, no backoff** (`utils/payment_retry.py:300`) — a transient Stripe rate-limit window means up to 15 min to final failure, and the loop can hammer Stripe; `processing` rows in the same query can starve `failed` ones (`:206`).
10. **Heavy background scans share the primary** (`repositories/_base.py:37`) — retention/reconciliation/T4A aggregations compete with hot-path ride inserts. No read replica for reporting.

---

## 💡 Tech Stack & Architecture Recommendations

**Current stack is sensible for the stage** (FastAPI + Supabase + Redis + Stripe + Firebase + Expo + Next.js, dual Railway/Fly with DNS failover). The gaps are *infrastructure maturity*, not framework choice. Highest-leverage additions, in order:

1. **Fleet-aggregated metrics + SLO alerting (biggest single win).** Ship per-process metrics to a real backend — **Prometheus + remote_write / Grafana Cloud, or Datadog**. Wire the existing `utils/metrics.py` names into OpenTelemetry exposition, add burn-rate alerts tied to the `docs/slo.md` targets. Without this, the documented SLOs are unmeasured.
2. **A real async job queue** for payment retries, push, reconciliation, T4A — **Redis-backed (arq / RQ / Celery)** or **Supabase pg_cron + a worker**. Replaces 23 fire-and-forget loops that re-scan on every crash and depend on hand-rolled replay safety. Durable retries with exponential backoff come for free.
3. **External synthetic monitoring** (Checkly/UptimeRobot/Grafana Synthetics) hitting `/health`, auth, and fare-estimate every 60 s from outside → PagerDuty (ACTION_ITEMS E4). Today a total outage is discovered by users.
4. **Feature flags / kill switches** for surge, scheduled dispatch, promo, corporate billing (ACTION_ITEMS E5). A misbehaving subsystem should be disable-able in seconds without a deploy. Pairs with gradual rollout (1%→100%).
5. **Per-operation-class circuit breakers** (`repositories/_base.py:62`) — one global breaker means a cold-start spike on a reporting query 503s auth + payments + dispatch. Split read/write and hot/batch.
6. **Connection pooling via Supabase/PgBouncer** or a direct asyncpg path for the hottest queries (dispatch candidate lookup, ride insert) — removes the sync-over-async thread-pool bottleneck for the paths that matter.
7. **A staging environment** (ACTION_ITEMS E1) — unblocks load-test execution (E2, harness already built), migration rehearsal, and DAST (E6). `main → prod` with no intermediate is the riskiest single process gap.
8. **Forced-upgrade gate for mobile** (ACTION_ITEMS E3) — cheap now, impossible to retrofit onto already-old binaries.
9. **WS reconnect replay handshake** — the outbox exists (`utils/ws_pubsub.py:172`) but clients never drain it, so a 30 s reconnect silently loses ride-status/location events. Wire a `get_outbox` call into the client reconnect path.

---

## 🛠️ Maintainability & Code Smells

- **Sync-over-async foundation.** The whole DB layer is `run_sync()` over a thread pool wrapping a synchronous Supabase client. It works and is carefully retried, but it's the root cause of findings #3, #6, #8, #10 above. A long-term migration to an async-native driver (asyncpg) for hot paths is the structural fix.
- **`any`/`as any` casts persist on the mobile clients** despite an active sweep (#551 in flight). Money fields are `parseFloat(... as any)` with no `MoneyString` validation (`ride-in-progress.tsx:84`, `payment-confirm.tsx:214`) → "$NaN" / "$-5.50" can render from corrupt data with no guard.
- **Migration prefix collisions** (08, 28, 29, 48, 50–58, …) — knowingly tolerated via full-filename idempotency keys and now blocked by CI, but a real readability/onboarding tax. The 2026-04-28 retention regression (PRs #138/#141 both on slot 56) is the concrete cost.
- **23 background loops in one `lifespan.py`** — a lot of operational surface in one file with bespoke replay-safety per loop. The job-queue recommendation (D2 above) is also a maintainability win.
- **Dual-import pattern** (`try: from .x / except: from x`) is intentional and documented — leave it, but it's the kind of thing that confuses new contributors.
- **Error-handling copy is decentralized** — no shared user-facing error map; each screen reinvents it.
- **Dead/legacy artifacts** noted in CLAUDE.md (`memory/`, `discovery/`, removed `carpool.tsx`) — housekeeping, low priority.

---

## 🧪 Testing & QA (missing edge cases)

- **Coverage floor is 60% globally; the ≥90% payments / ≥80% rides mandate is not CI-enforced** (`backend/pytest.ini:15`, `ci-guardrails.yml` is advisory `continue-on-error`). This is ACTION_ITEMS A1 — "the single biggest remaining gap" — and the right #1 priority. Ratchet per-path with `coverage report --fail-under`.
- **No per-route coverage *gating*** — a PR can drop payments from 92%→75% and CI only warns.
- **E2E (Playwright) is non-blocking on PRs** (`ci.yml:312`, `continue-on-error`) — admin-backed ride-state regressions reach the branch uncovered.
- **Post-deploy smoke doesn't exercise payment/Stripe-webhook endpoints** (`ci.yml:610`) — the highest-risk integration surface ships unverified (ACTION_ITEMS A2).
- **Load test exists but runs manually with no SLA gate in CI** (`loadtest/locustfile.py`) — an O(n) dispatch regression degrades P95 over days with no signal. Blocked on staging (E1/E2).
- **Specific missing edge-case tests** surfaced by this review: double-charge under crash-then-retry (C1), ride-wedged-in-processing (C2), pass-gate-fails-open (C3), audit-write-failure (C4), surge-truncation pricing (C7), 429 client countdown (telemetry §), WS reconnect replay (D2). Each deserves a regression test alongside its fix.
- **No SAST rules for money bugs** — Semgrep runs `p/python`/`p/owasp` but no custom Spinr rule for `Decimal→float` coercion or idempotency-key reuse (`security-gates.yml:86`). The pre-commit float hook covers fare code; broaden it.

---

## 📈 Manager's Verdict (overall code health)

**Grade: B+ / "strong regional-beta, not yet multi-region scale-ready."**

This is a disciplined, security-conscious codebase that has clearly absorbed hard lessons (the retention-regression postmortem, the MFA rollout, the float-money pre-commit hook, the honest backlog). The team's *process* is better than most Series-A shops. The substance of this review is that the remaining risk has shifted from "obvious bugs" to **three structural themes**:

1. **Money-path resilience** — a handful of concrete double-charge / wedged-ride / fail-open-gate edge cases (C1–C3) that are low-probability but high-consequence in a regulated, 0%-commission market where trust *is* the product. Fix these first.
2. **Observability & graceful degradation** — strong admin logging, but fleet-level metrics don't aggregate and user-facing errors aren't consistently sanitized or soft-failed. You can't defend an SLO you can't measure.
3. **Scale-readiness of the foundation** — sync-over-async + thread-pool + 23 in-process loops + per-replica state is fine at beta load and will bottleneck under 10×. None of it blocks launch; all of it should be on the post-launch roadmap before a marketing push.

**Versus Uber/Lyft:** the *product* model (0% commission, SK-regulatory-native, WAV dispatch, T4A) is genuinely differentiated and in some compliance respects ahead. The *engineering* delta is exactly what you'd expect at this stage — dispatch sophistication (no predictive/ETA-aware batching with offline driver-state cache), surge spatial indexing, connection pooling, message queues, feature flags, staging, and SLO tooling. These are maturity gaps, not design errors.

---

## Recommended Plan (sequenced)

**Sprint 1 — Money-path P0s (do not launch a marketing push without these):**
- C1 idempotency key → single immutable per-attempt token; regression test for crash-then-retry.
- C2 charge-ride null-PM → don't flip to `processing` until a chargeable PM is confirmed; surface a clean 402.
- C3 pass-gate → **fail closed** on subscription-table errors (503), with a metric + alert.
- C4 audit-write → make critical-mutation audits fail-fast / transactional.

**Sprint 2 — Telemetry & graceful degradation:**
- Ship `utils/metrics.py` to Prometheus/Datadog with fleet aggregation; wire `docs/slo.md` burn-rate alerts.
- Central user-facing error map; kill `error.message` leaks (C8/C9); narrow `catch (any)`→`unknown`+guard; 429 countdown UI; `recordApiError`→Sentry with `request_id`.
- C5 dispatch-status race + C7 surge-truncation pricing guard.

**Sprint 3 — Scale foundation (post-launch roadmap):**
- External synthetic monitoring + staging env (E1/E4) → unblocks load-test execution (E2) and DAST (E6).
- Per-path coverage gating in CI (A1) + payment smoke test (A2) + E2E blocking on PRs.
- Feature flags / kill switches (E5); WS reconnect replay; surge PostGIS auto-detect (D1).
- Begin async-job-queue migration for the background loops; per-operation circuit breakers; connection pooling on hot paths.

**Sprint 4+ — Industry parity:** driver destination mode (D3), driver heatmap (D4), read replica for reporting, CODEOWNERS for money/schema paths (E8), SBOM/provenance, license scan (E10), a11y in CI (E11).
