# Spinr — Comprehensive Engineering Teardown & Remediation Plan

**Date:** 2026-07-25 · **Type:** Read-only review (no code modified) · **Branch:** `claude/epic-planck-oizbsr`
**Method:** Five parallel domain reviewers (security/PII, money/payments, dispatch/rides, frontend/telemetry, architecture/stack) over the live tree (~292 backend files / ~101k LOC + rider/driver/admin/shared surfaces), cross-referenced against the team's own tracked backlog in `ACTION_ITEMS.md` (Sections A–E) and `sprint-current.md`.

> **Headline:** This is a genuinely well-engineered codebase. The security auditor found **zero blockers** in the reviewed auth/JWT/OTP/Stripe/CORS surfaces; the money auditor found **no fund-loss or double-charge bug**; the two invariants most likely to break in dispatch (acceptance race, cancel-after-in_progress) are both **correctly handled with atomic DB claims**. The findings below are refinements, consistency debt, and scaling-runway items — not a system in trouble. What separates Spinr from a market leader is not correctness of the core flows; it is **operational maturity** (job-queue isolation, distributed tracing, connection pooling, per-path coverage gates, feature flags) and a handful of **telemetry gaps** where failures are handled for the user but never reach an admin dashboard.

---

## 🚨 Critical Issues & Security Flaws

No launch-blocking security vulnerability was found in the audited surfaces (JWT trust model, OTP, Stripe idempotency, CORS, admin authz are all correctly implemented). The items below are the highest-severity *real* defects surfaced.

| # | Severity | Location | Issue & Root Cause | Impact |
|---|---|---|---|---|
| C1 | High | `routes/admin/rides.py:948` | **PIPEDA leak — admin manual-assign FCM push sends the full payload including `rider_name`.** The auto-dispatch path deliberately strips PII via `_FCM_EXCLUDE` (`matching.py:884`); the admin path re-introduces it. FCM data is cleartext in the device tray on US/Google infra. | Personal data (rider name) exposed on third-party US infra — a PIPEDA data-residency/minimization violation on a live code path. |
| C2 | High | `routes/wallet.py:200` | **Stripe API version not pinned on wallet top-up.** Uses `stripe.api_version` (SDK global) instead of the `STRIPE_API_VERSION` constant. `payments.py:1011` documents exactly why this is wrong and does it correctly — wallet.py is the one path that missed the fix. | EphemeralKey issued against a different API version than the webhook expects → PaymentSheet field desync for top-ups, invisible until a version bump or cold-start race. |
| C3 | High | `features.py:894` + `fare_service.py:197` | **Possible double-charged airport surcharge.** Two independent admin-configurable mechanisms (`service_areas.airport_fee` and an `area_fees` row with `fee_type="airport"`) both gate on the *same* `in_airport` polygon check with no mutual-exclusion. | If an operator configures both for one zone, riders are charged the airport fee twice — each line looks individually correct, so it is "transparent but wrong." |
| C4 | Medium | `services/payment_service.py:433` | **Corporate settlement has no rider-card fallback.** Documented priority chain is "rider wallet → allowance → master wallet → rider card"; the implemented chain stops at master wallet and strands the fare in `payment_status="pending"`. | A depleted/frozen corporate master wallet can strand fares indefinitely — revenue-collection risk if ops don't watch the retry backlog; also a doc/code mismatch. |
| C5 | Medium | `migrations/17_corporate_accounts_fk.sql:122` & `migrations/06_cloud_messaging.sql:97` | **Two `FOR ALL` RLS policies** violate the house rule ("never `FOR ALL` on user-writable tables"). `corporate_accounts` grants role='admin' unrestricted CRUD with no `WITH CHECK` and no module-scoping; push-tokens is lower-risk (USING scopes to `auth.uid()`) but still unenumerated. | If any admin surface ever queries Supabase directly with the anon key + staff JWT, a plain `admin` gets un-audited CRUD on billing entities. Defense-in-depth gap. |

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

The client-side error *presentation* stack is a strength: `shared/api/client.ts` `getApiErrorMessage`/`extractError`, `shared/errors/errorPresentation.ts`, and `notifyError` strip Axios noise, handle 429 with countdowns, redact GPS from logged URLs, and gate raw errors behind `__DEV__` in `ErrorScreen.tsx`. The failures are on the **admin-logging half** of the objective — exceptions that are handled gracefully for the user but never reach an admin.

| # | Severity | Location | Issue | Impact |
|---|---|---|---|---|
| T1 | High | `admin-dashboard` Sentry wiring | **Sentry is a dependency but never loads.** `sentry.client/server.config.ts` call `Sentry.init`, but there is no `instrumentation.ts`/`instrumentation-client.ts`, no `withSentryConfig` in `next.config`, and no `global-error.tsx`. Both error boundaries only `console.error` — yet `dashboard/error.tsx:25` tells the admin "The error has been logged." | Admin-dashboard crashes produce **zero** telemetry while the UI claims otherwise. |
| T2 | High | `shared/api/client.ts:878` (`TODO(diag)`) | **API errors never reach Sentry.** `recordApiError` only pushes to an in-memory ring buffer + `console.log`. Every 4xx/5xx across both mobile apps — including payment, dispatch, and auth failures — is invisible to Sentry even though Sentry *is* initialized in the apps. | The single highest-volume failure channel has no aggregated monitoring — directly violates "all exceptions logged with enough context for admins to monitor." |
| T3 | High | `shared/components/ErrorBoundary.tsx:40` | **Raw stack trace shown to end users in release builds.** Renders `error.stack` and `${error.name}: ${error.message}` on the crash screen with a comment stating this is intentional ("not just `__DEV__`"). | Direct violation of "no raw errors/stack traces/jargon leak to the end-user." `ErrorScreen.tsx:75` already does this correctly — the boundary is the outlier. |
| T4 | Medium | `admin-dashboard/src/app/error.tsx:14` | Root `GlobalError` renders `{error.message}` unconditionally, unlike `dashboard/error.tsx` which gates behind `NODE_ENV !== "production"`. | Technical React error strings leak to admins in prod; console-only logging. |
| T5 | Med (Regulatory) | `routes/scheduled_rides.py:225`; `routes/webhooks.py:836` | Scheduled 10-min reminder is **not atomically claimed** (Redis leader lock fails *open*) → duplicate reminder pushes on a Redis blip. Separately, `charge.refunded` sets `payment_status="refunded"` for **partial** refunds too (no `amount_refunded` vs total compare) → partial goodwill refund is indistinguishable from a full reversal in T4A/tax reporting. | Duplicate notifications; refund-reporting/tax-reconciliation ambiguity. |

**Root cause pattern:** the team invested heavily in *user-facing* soft-fail UX and *server-side* structured logging, but the **client→Sentry bridge** and the **admin-dashboard→Sentry bridge** were both left as TODOs. This is the single cheapest, highest-leverage fix in the whole report.

---

## 🐢 Performance Bottlenecks & Optimizations

| # | Severity | Location | Bottleneck | Fix |
|---|---|---|---|---|
| P1 | Med-High | `routes/rides/matching.py:795` | **N+1 `quest_progress` read inside the serial offer-notify loop**, and offer WS sends are awaited sequentially. With `max_offers=10`, driver #10's offer waits behind 9 quest queries + 9 sends — threatens the **<2s dispatch SLA**. | Batch quest rows with one `.in_()` before the loop; fan WS sends with `asyncio.gather`. |
| P2 | High (scale) | `db_supabase.py` / `supabase_client.py` | **No real connection pooling.** Every query is a PostgREST HTTP round-trip through a 64-thread executor (HTTP/1.1-pinned), no persistent Postgres pool, no prepared statements. The elaborate circuit-breaker/retry/GOAWAY handling is a *workaround* for a fragile transport. Throughput ceilings at 64 in-flight queries/worker. | Front with **Supavisor** (txn mode, :6543) or **PgBouncer**; move the 5–6 latency-critical queries (dispatch candidates, fare reads, presence) to **asyncpg**. Highest-leverage single change. |
| P3 | Med | `utils/surge_engine.py` (ACTION_ITEMS D1) | Surge query caps at 500 drivers with a Python haversine loop; no PostGIS. | Push spatial filtering into PostGIS `ST_DWithin` with a GIST index (already tracked as D1). |
| P4 | Med | `routes/rides/matching.py:328` | Presence "reachable-but-empty ⇒ empty the pool" **stalls dispatch fleet-wide** during a Redis flush/failover until ~1 Hz heartbeats repopulate. | Add a short fail-open grace window after a detected presence-store reset. |
| P5 | Low | `repositories/_base.py` | Caching is row-level only (30s TTL on `user:`/`driver:`). Read-mostly aggregates (service-area/surge/fare-config, driver-eligibility checked on **every** `go_online`) hit PostgREST every time. | Extend the existing Redis cache (invalidation plumbing already exists) to those reads; consider Cloudflare edge caching for public endpoints. |

---

## 💡 Tech Stack & Architecture Recommendations

The topology (single FastAPI process + Supabase + Redis + one Redis pub/sub WS channel) is **correctly simple** for a Saskatchewan-first, 0%-commission startup. The recommendations below are the ones that transfer from big-tech practice *without* over-engineering.

1. **Isolate background work from request-serving.** There are **29** in-process asyncio loops (CLAUDE.md says 16 — doc is stale) running on *every* uvicorn worker × every machine. Replay-safety is handled per-loop, but work multiplies by worker count, a stalled loop degrades API latency on the same process, and there's near-zero job observability. → Move scheduled/deferred work to a dedicated worker tier: **Arq** (async-native, lightest lift) for most loops, **Temporal** for the durable money/reconciliation sagas (`reconciliation`, `stripe_reconcile`, `t4a_annual_job`, payment retry). Cheap interim step: gate loop startup behind `RUN_BACKGROUND_LOOPS=1` so only one tier runs them.
2. **Connection pooler + async DB path** (see P2) — table stakes, not scale-chasing.
3. **OpenTelemetry distributed tracing.** No OTel today; you cannot follow one ride's offer→accept→settle path across the loops and thread pool. Sentry `traces_sample_rate=0.1` is coarse. → Auto-instrument FastAPI + httpx + redis, export to Tempo/Honeycomb/Sentry Performance; add `trace_id` to the structured logs and WS envelope (ACTION_ITEMS D2). **The one big-tech practice with immediate ROI for a small team.**
4. **Fix per-process metrics.** `utils/metrics.py` is an in-process shim that resets on restart and isn't aggregated across workers, so `spinr_payment_settlement_total` and friends undercount fleet-wide. → Swap to `prometheus_client` multiprocess mode (`PROMETHEUS_MULTIPROC_DIR`) scraped into Prometheus/Grafana. Metric *names* already follow conventions — mostly a transport swap.
5. **Make Redis mandatory (fail-fast) in production.** The in-process dict fallback is *per-process*; with 2–4 workers, OTP lockout, rate-limit buckets, and leader locks silently split across processes. Startup only `logger.error`s, doesn't fail. → Fail-closed for the auth-security paths; gate startup on Redis readiness.
6. **Feature flags / kill-switches.** No LaunchDarkly/Unleash/Flagsmith; `app_settings` is config, not per-cohort flags. Mobile clients can't be instantly rolled back. → **Unleash** (self-hostable, Canadian-residency-friendly) for server-side kill-switches on dispatch/surge/payment paths (ACTION_ITEMS E5).
7. **Collapse inconsistent worker config.** Root `Dockerfile` `--workers 1`, `backend/Dockerfile` `4`, `fly.toml` `2`. Because loops are in-process, effective background load silently changes by deploy target. → One Dockerfile, one documented worker env var.

**vs Uber/Lyft:** Their dispatch (H3 hex-indexing + Ringpop-sharded stateful matching), streaming supply/demand (Kafka + Flink), ML surge, and multi-DC stateful stores are the *right* altitude for many-city scale and **textbook over-engineering for Spinr today** — do not build them early. What genuinely transfers *now*: connection pooling + async hot paths, background-work isolation, OpenTelemetry, and feature flags. Everything else (H3, Kafka, ML surge, sharded matchers) should be flagged if proposed prematurely.

---

## 🛠️ Maintainability & Code Smells

- **Decimal/float discipline is 95% there, with defense-in-depth gaps.** `surge_engine.py` computes tiers as plain floats outside the pre-commit hook's scope (safe only because tiers are binary-exact today); `fare_service.py:294` uses `float(surge_multiplier)` in a receipt label; `payment_service.py:427/437/454` does a `_f()` Decimal→float hop *one step before a money RPC* (the docstring says `_f()` is display-only); `corporate_allowance_service.py:42` skips the `quantize` step its sibling `corporate_wallet_service.py` does. All safe today, all fragile to the next edit. → Make surge constants Decimal, pass rounded Decimals straight into RPCs, add the missing quantize.
- **Documented-but-inert surge override.** Admin can set/justify/audit-log an override up to 10× (`admin/service_areas.py:517`), but `fare_service.py:438` unconditionally clips to `min(surge, SURGE_CAP)` — the override is stored and logged but never charged. Safe direction, but a silent operator-expectation gap; surface "overrides >2.5× are audit-only" at write time.
- **Near-dead parallel API client.** `shared/api/cachedClient.ts` surfaces raw backend `detail` verbatim, has no 401-refresh/redaction, and reads a SecureStore token the main client keeps in memory (so it always 401s). Only self-imported — a live footgun. → Route through the main client or delete.
- **Raw `asyncio.create_task` without retaining handles** (`scheduled_rides.py:210`, `admin/rides.py:951`, `safety.py:114`) — task can be GC'd mid-flight; the rest of the codebase uses `_deps.spawn`. → Route through `spawn`.
- **Stale documentation:** CLAUDE.md says "16 background loops"; there are 29. Migration count/next-slot notes go stale (now at migration ~249). Keep the counts honest or stop citing exact numbers.
- **Deprecated `frontend/` still in CI** renders raw Axios strings to users in 6 screens — quarantine hazard; migrate or remove from CI.

---

## 🧪 Testing & QA (Missing Edge Cases)

- **Per-path coverage floors not enforced** (ACTION_ITEMS A1 — "the single biggest remaining gap"). CI floor is a flat **60%** (`pytest.ini --cov-fail-under=60`), but CLAUDE.md mandates ≥90% for `payments.py`/`fare_service.py`/`crypto.py` and ≥80% for `rides.py`/`dispatch_service.py`. A payments PR can land at 62% aggregate while `fare_service.py` slips. → `diff-cover` on PRs + per-package `fail_under`.
- **Load test exists but is not wired to CI.** `loadtest/locustfile.py` is real; no workflow runs it, and the documented P95 SLAs (dispatch <2s, fare <300ms) are unenforced — regressions ship silently until the KPI dashboard notices. → Nightly Locust against staging + a `k6` PR smoke with threshold assertions (ACTION_ITEMS E2/E4).
- **No API contract tests** across the 4 clients sharing one FastAPI contract. `shared/` TS types can drift from the Python Pydantic schemas with nothing catching it until an E2E break or a field production failure. → Generate OpenAPI from FastAPI, run **Schemathesis** in CI, drive `@spinr/shared` types via `openapi-typescript` so drift is a compile error.
- **Uncovered edge cases surfaced by this review that deserve regression tests:** partial-vs-full refund status (T5), airport-fee double-config (C3), corporate master-wallet-depleted settlement path (C4), duplicate scheduled-reminder under Redis failover (T5), presence-reachable-but-empty dispatch stall (P4), and the unmount-before-await GPS-watcher leak in `driver-app/hooks/useDriverDashboard.ts` (~646).
- **No rate limiting on `disputes.py`** (all 5 endpoints, file never imports the limiter): `create_dispute` fires a push + a Zoho ticket task per call, and `admin_resolve_dispute` triggers a live Stripe `Refund.create` — both spammable beyond the generic 100/min default. → Add `payment_action_limit` to create + resolve.

---

## 📈 Manager's Verdict — Overall Code Health

**Grade: B+ / strong pre-launch.** This is a disciplined, security-conscious codebase that has clearly been through multiple hardening sprints. The money and dispatch cores — the two places a ride-share platform actually dies — are correct: atomic acceptance races, idempotent Stripe handling, compensating-transaction sagas, Decimal-based fares, 0% commission provably preserved, no fund-loss path found. The team's own `ACTION_ITEMS.md` already tracks most of the *architectural* gaps this review independently rediscovered (coverage floors A1, distributed tracing D2, PostGIS surge D1, feature flags E5, load-testing-in-CI E2/E4), which is itself a sign of healthy engineering self-awareness.

**Where it is genuinely behind a market leader is operational, not architectural:** (1) **telemetry has holes** — client API errors and admin-dashboard crashes never reach Sentry despite UI copy claiming they do (T1/T2), the highest-ROI fix here; (2) **background work is entangled with request-serving** across 29 in-process loops, which caps how the API tier can scale and hides job failures; (3) **no connection pooling** puts an early ceiling on DB throughput; (4) **no per-path coverage or performance-regression gates** means the documented SLAs and coverage minimums are aspirational.

**Recommended sequencing (impact × effort):**

- **This week (cheap, high-impact):** wire the client→Sentry bridge (T2) and admin→Sentry bridge (T1); gate the release-build stack-trace leak behind `__DEV__` (T3); apply `_FCM_EXCLUDE` to the admin push path (C1); pin `STRIPE_API_VERSION` in `wallet.py` (C2); rate-limit `disputes.py`.
- **This sprint:** the airport-fee double-charge guard (C3) + regression test; per-path coverage gates (A1); batch the dispatch quest N+1 (P1); make Redis fail-fast in prod (#5); corporate card-fallback decision (C4).
- **Next quarter (runway):** Supavisor/PgBouncer + asyncpg on hot paths (P2); background-work isolation via Arq, starting with money/dispatch-recovery loops; OpenTelemetry (D2); feature flags (E5); Locust/k6 in CI (E2/E4).

Nothing here is a five-alarm fire. The plan is: **close the telemetry blind spots first** (so you can *see* production), **then buy scaling runway** (pooling + job isolation), **then raise the safety net** (coverage + perf gates + flags). Ship the this-week list before the next payments- or dispatch-touching release.

---

*Findings are cross-referenced to `ACTION_ITEMS.md` (Sections A–E) where the team already tracks them. No code was modified in producing this review.*
