# Spinr — Engineering Director Architecture Teardown

**Date:** 2026-07-18
**Scope:** Read-only review of all five surfaces (backend, rider-app, driver-app, admin-dashboard, shared) benchmarked against ride-share market leaders (Uber, Lyft).
**Method:** Four parallel evidence-gathering passes (backend correctness/security, performance/scalability, frontend/mobile, tooling/CI/observability). Every finding cites `file:line` against the tree at HEAD (`ca04f5a`).
**Nature:** No code was modified. This is a teardown + prioritized plan, not a patch.

---

## Executive framing

This is **not a generic Uber clone**, and it does not read like typical pre-launch code. The money discipline (Decimal-only, `_d/_round/_f`), Stripe idempotency (event-claim + wallet-credit dedup), ride-state atomic conditional-UPDATE guards, JWT trust model, OTP hardening, PIPEDA-grade Sentry scrubbing, and Saskatchewan regulatory awareness are implemented to a standard most Series-B ride-share startups never reach. Inline comments cite the specific bug each guard fixed.

The remaining work is therefore **operational-readiness and a short list of concrete fixes**, not an architectural rewrite. The four agents surfaced **1 High**, ~**8 Medium**, and a tail of Low/doc-drift items. No Critical live-defect was found.

---

## 🚨 Critical Issues & Security Flaws

There are **no Critical live security defects.** The highest-severity items are one High-load reliability bug and one latent data-integrity landmine.

### HIGH — `referral_payout_loop` full-table-scans on every replica, every 5 min, with per-user N+1
`utils/referral_payout.py:72,110,117,143`
- **What:** `_tick()` has **no leader lock** (unlike `surge_engine` / `scheduled_rides`). Every run does two unfiltered scans — `get_rows("referral_payouts", {}, limit=20000)` and `get_rows("users", {}, limit=10000)` — then loops every referral-code user issuing per-user completed-ride counts. `referral_terms.py:187,211` adds two more 10k scans.
- **Why it matters:** O(users) N+1 running on **N replicas simultaneously** every 5 minutes. Correctness is protected by the `referral_payouts` UNIQUE claim, but **load is not**. At 10k users this is 10k+ queries × replicas / 5 min against Supabase — a self-inflicted DB brownout that will breach every P95 SLA on the same connection pool.
- **How (fix):** (1) Add a Redis leader lock (`redis_set_nx`, copy the `surge_engine.py:373` pattern) so only one replica runs it. (2) Replace "scan all users, filter for non-null `referral_code` in Python" with a filtered query / RPC or a `pending_referrals` rollup table. (3) Batch the per-referee ride counts via `.in_()`.

### HIGH (latent) — dead **and unsafe** offline queue in the mobile client
`shared/api/offlineQueue.ts`
- **What:** A persisted replay queue is fully implemented but has **zero call sites** — `initOfflineQueue`/`enqueueRequest` are never invoked. So the documented "queue writes and replay on reconnect" behavior **does not run today**. Worse, `processQueue` (`:152-158`) replays POSTs **with no idempotency key**.
- **Why it matters:** As written, wiring it would replay `POST /rides` or a payment after reconnect → **double-booked rides / double charges**. Right now it's misleading dead code that reads as "we have offline support" in reviews and planning graphs.
- **How (fix):** Either delete the module, or — before ever wiring it — add per-request idempotency keys and an allowlist of safe-to-replay endpoints (never blind-replay ride/payment POSTs).

### MEDIUM — minute-bucketed Stripe idempotency fallback collides on distinct top-ups
`routes/payments.py:224,1008`
- **What:** For non-ride intents with no client key, the fallback idempotency key is `f"intent-{user_id}-{int(time()//60)}"`. Two *distinct* wallet top-ups by the same user within the same 60s window collide; Stripe returns the first intent → the second top-up silently reuses the first's amount.
- **Why:** A time bucket is being used as a uniqueness token.
- **How:** Require/generate a per-request `client_idempotency_key` (the request models already accept it); never fall back to a coarse time bucket for money intents.

### MEDIUM — ErrorBoundary renders raw stack/component traces to production end-users
`shared/components/ErrorBoundary.tsx:38-55`
- **What:** Deliberately shows `componentStack || error.stack` on-device "not just in `__DEV__`". On a **consumer rider** surface, a red stack trace is poor UX and leaks internal module structure.
- **How:** Gate the raw diagnostics behind `__DEV__`/staff builds; show users a friendly summary + `requestId`. Sentry (`captureException`) already carries the full trace for admins.

---

## 🛡️ Error Handling & Telemetry (user experience vs. admin logging)

**This is a genuine strength and largely lives up to the "do not silently swallow" rule.**

- **User-facing surfaces are clean.** No raw error / stack-trace / PII leakage observed in API responses. Mobile call sites funnel through `getApiErrorMessage()` (`shared/api/client.ts:417-464`) which strips Axios's generic "Request failed…" and prefers the backend's human copy. Card declines return Stripe's safe `error_message`; internal exceptions log server-side and return generic 402/503.
- **Admin/backend logging is deep.** DB errors unwrap `DatabaseError.details["original"]` (`webhooks.py:464`, `auth.py:739,844`) exactly as the conventions require. Financial ledger writes log `ERROR` with `exc_info` and never raise (RPC already moved the money atomically). The auth path deliberately fails **closed** (503) instead of the old swallow-and-create-phantom-account path.
- **Fail-open vs fail-closed is deliberate and correctly asymmetric:** security gates (OTP lockout, subscription-required dispatch filter) fail **closed**; supply-side dispatch presence/quota filters fail **open** with `spinr_dispatch_presence_filter_failed_total` emitted. This is the right call.
- **The one real gap** is the ErrorBoundary above (Medium). Otherwise telemetry is strong: Sentry DSN-gated with a PIPEDA scrubber (`utils/sentry_scrub.py`) redacting phones/emails/coords, a loguru→Sentry bridge (`server.py:476`) so `logger.error` actually reaches Sentry with `domain`/`ride_id` promoted to tags, and `X-Request-ID` propagation for correlation.

**Missing:** nothing *scrapes or alerts on* the metrics (see Performance §Monitoring). Great telemetry with no alarm bell attached.

---

## 🐢 Performance Bottlenecks & Optimizations

Hot paths are, with the exceptions below, **well-optimized** — `run_sync` thread-pool with a half-open circuit breaker + per-second retry budget + deadline propagation; dispatch N+1s collapsed to `.in_()` batches + Redis `MGET`; admin monitoring fully batched; Stripe fully `asyncio.to_thread`-offloaded; Maps async + multi-layer Redis cache + spend budget + per-path timeouts (1.2s in dispatch, 0.5s in estimate); WS targeted fan-out with per-message 2s timeouts.

| Sev | Bottleneck | Location | Why / Fix |
|---|---|---|---|
| HIGH | `referral_payout_loop` scan+N+1 on every replica | `utils/referral_payout.py:72,110,117` | See Critical §. Leader-lock + filtered query + batch. |
| MED | Surge supply count is Python-side, capped at 5000 rows | `utils/surge_engine.py:46,51,161-196` | Above the cap, supply is truncated → **surge over-stated** (a regulated price). PostGIS path (`_count_supply_spatial`) exists but is flag-off (`SURGE_SPATIAL_COUNT`). Enable the `drivers_available_in_polygon` RPC → index-only, no cap. |
| MED | Admin driver-location WS fan-out is O(drivers × admins) | `socket_manager.py:377-386` | Every driver's location goes to every admin socket (throttled 3s, `durable=False`, but no viewport filter). Add an admin bbox/viewport-subscription protocol. Protects `spinr_ws_fanout_duration_ms < 100ms`. |
| MED | Fare-estimate handler = long chain of **sequential** DB awaits under a 300ms P95 | `routes/rides/estimates.py:148-397` | `asyncio.gather` the independent lookups (fares, drivers, airport fee, area resolution); cache the `service_areas` read (changes rarely) instead of reading 500 rows per estimate. |
| LOW | Redis in-process fallback silently degrades rate-limit/OTP/WS across replicas | `redis_client.py:20`, `rate_limiter.py:43` | Mitigated by fail-closed OTP + prod ERROR log. Depends entirely on Redis always being provisioned in prod. |
| LOW | OFFSET-only pagination, no keyset primitive | `repositories/_base.py:603` | Deep admin pages degrade; add keyset/cursor pagination before large lists. |

---

## 💡 Tech Stack & Architecture Recommendations

The stack is **current** (FastAPI 0.136, Pydantic 2.13, Python 3.12, supabase 2.29, redis 7.4, stripe 15.1, sentry-sdk 2.59), pip-compiled and SHA-256 hash-pinned with `--require-hashes` in CI. This is not a legacy codebase. The substantive recommendations are architectural fit, not version bumps:

1. **Data layer ceiling — `supabase-py` over a thread pool.** `db_supabase.py` wraps the *sync* supabase-py client in `run_sync()` on a 64-thread executor. In a fully-async app this pushes every DB call onto GIL-bound executor threads — a throughput ceiling under load, directly on the dispatch/settlement hot path. **Recommend:** migrate hot-path reads/writes to **asyncpg** (or SQLAlchemy 2.x async / `psycopg` 3) against Supabase Postgres directly, keeping supabase-py only where RLS/PostGREST semantics are actually needed. (`psycopg2` present via `migrate.py` is maintenance-only; prefer `psycopg` 3 for new code.)
2. **No connection pooler.** Horizontally-scaled replicas × direct Postgres connections will exhaust connection limits. **Recommend:** front Postgres with **PgBouncer / Supabase Supavisor** in transaction mode before launch (mind asyncpg prepared-statement caveat).
3. **Spatial matching.** Dispatch uses a bounding-box pre-filter + Python haversine per tick. This is correct and fast at city scale, but the industry path (and the surge fix above) is **PostGIS**, and at metro scale **H3 hexagonal indexing** (Uber's DISCO/H3 approach). Recommend enabling the existing PostGIS RPCs first; H3 is a later, optional lever.
4. **Metrics exporter + tracing.** The hand-rolled Prometheus shim is fine now; the natural next step is `prometheus_client`/OpenMetrics + **OpenTelemetry** SDK once a scraper/alerting exists. Code comments already anticipate this. (Full OTel is a *documented, conscious deferral* in `ACTION_ITEMS.md`, not an oversight.)
5. **Trim request-path weight.** `pandas 3.0` + `numpy 2.4` + `pyiceberg` are heavy for a request-serving backend — confirm they're not imported on the hot path / in the container's resident memory.

---

## 🛠️ Maintainability & Code Smells

- **`driver-app/hooks/useDriverDashboard.ts` is a 1,528-line god-hook** driving the driver's hottest screen (WS lifecycle + location tracking + ride-offer state + countdowns + reconnect). Every state tick re-renders the whole subtree. **Decompose** into `useDriverSocket` / `useLocationTracking` / `useRideOffer` and push high-frequency values (lat/lng, countdown) into a Zustand slice with selector subscriptions. This also aligns with the repo's own ≤3-file working-style rule.
- **Doc drift (several):** `ARCHITECTURE.md` says Expo SDK 54 (actual 55) and Railway-only hosting (actual Railway **+** Fly.io dual, per `deploy-fly.yml`); `DEPLOYMENT.md` never mentions Fly (the intended primary); CLAUDE.md says admin access-token TTL 12h (code default is **1h**, `core/config.py:55` — code is *more* secure, doc is stale); CLAUDE.md says "16 background loops" (actual ~28 with restart supervision). None are security risks; all mislead the next maintainer.
- **Web storage bucket mismatch:** `client.ts:252` reads `sessionStorage` for `auth_token` but the 401 cleanup writes `localStorage` (`:913`). Near-nil impact (web uses HttpOnly cookies) but a latent trap.
- **Two divergent offer-timeout handlers** — legacy `_offer_timeout_handler` (30s) alongside the live batch handler (15s) in `matching.py`. Remove the dead one or document.
- **Dead Codecov config:** `ci.yml:24-69` `frontend-test` targets the deprecated `frontend/` dir yet still uploads coverage.

---

## 🧪 Testing & QA (missing edge cases)

**Strong baseline:** 344 backend test files, `asyncio_mode=auto`, a rich `conftest.py` (mock Supabase/Firebase/SMS/Redis + autouse external-dep patching across dual-import paths), real E2E ride-lifecycle suites (`test_e2e_ride_lifecycle`, `_cancellation`, `_payment_guard`, `_sos_flow`, `_wav_dispatch`, `_rating_regression`, 3× corporate), and perf baselines. All three frontends run Jest/Vitest + Playwright in CI.

**Gaps that matter:**
- **Coverage floor is a global `--cov-fail-under=60`, well below the CLAUDE.md per-domain minimums** (payments/fare/crypto ≥90%, rides/dispatch ≥80%). A regression that guts `services/fare_service.py` coverage passes as long as the aggregate stays ≥60% — the **highest-risk money/dispatch code has no coverage backstop**. Add component-level thresholds via `codecov.yml` or `diff-cover`.
- **Codecov is informational only** (no `fail_ci_if_error`, no repo-side gate). Backend **ruff lint is `continue-on-error: true`** — style/lint regressions don't block.
- **Playwright E2E not clearly wired into the blocking PR gate** — only unit runs in `ci.yml`; confirm browser E2E actually runs on PRs.
- **Missing edge cases worth explicit tests:** wallet top-up idempotency-collision (the Stripe Medium above), `confirm_payment` vs webhook tip-in-"owed" divergence (`payments.py:447-450` vs `webhooks.py:594-598`), offline-queue replay safety (once decided), and admin WS viewport fan-out.

---

## Benchmark vs. Uber / Lyft

| Dimension | Uber / Lyft | Spinr | Read |
|---|---|---|---|
| **Monetization** | 20–30% per-trip commission | **0% commission + SaaS (Spinr Pass)** | Deliberate differentiator, not a gap. Guard it. |
| **Surge** | Unbounded dynamic pricing | **Hard 2.5× cap, disclosed pre-booking** | Regulatory/brand choice — a *feature*, not a limitation. |
| **Dispatch** | H3 hex index + batch-window global optimization (DISCO) | Bounding-box + Python haversine, greedy nearest, per-tick | Correct at Saskatchewan scale; PostGIS is the next lever, H3 optional later. No batch global-matching yet — acceptable for target density. |
| **Data platform** | Sharded MySQL/Schemaless + service mesh | Single FastAPI process + Supabase, thread-pool DB, no pooler | Fine for the target market; the pooler + async driver are the scaling ceiling to address before growth. |
| **Observability** | M3 + Jaeger full distributed tracing, canary everywhere | Request-ID correlation, Prometheus shim, Sentry; OTel + canary deferred | Reasonable for scale; monitoring/alerting is the real gap (below). |
| **Release safety** | Heavy staging + canary + feature flags + kill switches | `main` → dual-prod directly; no staging, no feature flags beyond `app_settings` | **Biggest operational gap.** |
| **Driver retention features** | Destination mode, heatmaps, quests | Quests ✓; **destination mode & heatmap UI missing** (`ACTION_ITEMS` D3/D4) | Real feature-parity gaps for driver retention. |
| **Compliance** | Country-agnostic, retrofitted per market | **PIPEDA + Saskatchewan Transportation Act + SGI baked in** | Ahead of the leaders *for this market*. |

---

## 📈 Manager's Verdict — Overall Code Health

**Health: High. Grade: strong pre-launch, well above peer median.** This codebase is disciplined, security-conscious, and regulatorily self-aware in ways that are rare at this stage. The core engine — money math, idempotency, ride-state atomicity, dispatch race handling, auth trust model, PII scrubbing — is production-grade and defends itself with tests and inline rationale. There is **no case for an architectural rewrite**, and several patterns (single-flight token refresh, SOSButton safety states, fail-closed security gates, admin batched monitoring) should be treated as reference implementations.

The gap between "this codebase" and "launch-ready at scale" is **operational readiness plus a short fix list**, in this order:

**Ship-blockers (do before launch):**
1. **HIGH** — Leader-lock + de-scan `referral_payout_loop` (`utils/referral_payout.py`). Self-inflicted DB brownout under load.
2. **Operational** — Stand up a **staging environment with a gated prod promotion** (GitHub `environment:` + smoke/E2E gate). `main` → dual-prod-simultaneously with no gate is the single highest-leverage CI/CD gap.
3. **Operational** — **Synthetic monitoring + SLO alerting.** The metrics and manual-DNS Railway↔Fly failover are useless if nothing scrapes/alerts; a Fly outage is currently invisible until a human notices. Add Prometheus scrape + Alertmanager (or Grafana Cloud/Checkly) against `/metrics` and `/health`.
4. **HIGH (latent)** — Resolve the **dead/unsafe offline queue** (delete, or make replay idempotent before wiring).

**Fast-follows:**
5. Stripe idempotency time-bucket fallback (`payments.py`). 6. ErrorBoundary stack-leak gate. 7. Per-domain coverage enforcement (90/80). 8. Enable PostGIS surge count. 9. Connection pooler (PgBouncer/Supavisor) + async DB driver plan. 10. Admin WS viewport filter. 11. Fare-estimate `gather` parallelization. 12. DAST (ZAP) + a11y (axe-core) gates — WCAG is a *regulatory* obligation here, not a nicety.

**Housekeeping:** decompose the driver god-hook; reconcile the four doc-drift items; flip Gitleaks/ruff to blocking; publish the SECURITY.md PGP key; add SBOM (Syft).

Nothing here is alarming. The team has been building the right things in the right order; the remaining list is finite, well-understood, and mostly operational.

---

*Generated by an automated Engineering Director review routine (read-only). All findings are `file:line`-cited; no source was modified.*
