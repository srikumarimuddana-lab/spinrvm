# Spinr — Chief-Architect Code & Ecosystem Review

_Read-only teardown. 2026-07-02. Branch `claude/epic-planck-4dmdzp`. Surfaces reviewed: backend (FastAPI, ~85k LOC), rider-app, driver-app, admin-dashboard, shared. Benchmarked against Uber/Lyft-scale patterns._

> **Headline:** This is a genuinely mature, defensively-coded, well-documented codebase — far above the norm for a pre-launch platform. The JWT trust model, Stripe idempotency, OTP hashing/lockout, PII-in-logs discipline, migration-safety CI, and security SAST gates are all correctly implemented and should not be re-litigated. The gaps that remain are **scaling ceilings** (dispatch/DB), **a handful of real error-leak bugs**, **dead offline-resilience code**, and **environment/tooling** maturity (staging, per-module coverage gates, DAST, tracing). None are "the code is bad"; they are "the code is ready for beta, not yet for a dense-market Uber/Lyft-scale launch."

---

## 🚨 Critical Issues & Security Flaws

| # | Finding | File | Why it matters | Fix (the "how") |
|---|---|---|---|---|
| C1 | **Raw Stripe error strings leak to riders.** Non-decline `StripeError` (`invalid_request`, `api_error`) is returned as `error_message = str(e)` with `status_code=402`, so the 5xx sanitizer never scrubs it. Strings like `"No such customer: cus_…"` reach the end user. | `services/payment_service.py:776,434` ← `utils/stripe_charge.py:251,255` | Internal identifiers + parameter names exposed to users; also an information-disclosure vector. Uber/Lyft surface only "payment couldn't be processed, try another card." | For `status="failed"` (non-decline) return a static rider-safe message; keep `str(e)` server-side in `logger.error(exc_info=True)` only. |
| C2 | **Dispatch runs a full-table driver scan + Python haversine; PostGIS index is dead code.** `get_rows("drivers", {...}, limit=500)` then Python distance filter, per ride per retry. Spatial RPCs are deliberately bypassed because `update_driver_location` never populates the PostGIS `location` geometry. | `routes/rides.py:696-722`; `services/dispatch_service.py:284-297`; `repositories/driver_repo.py:107,119-137,195` | **The single biggest divergence from Uber/Lyft.** They use S2/H3 cell indexes → bounded candidate set. Here it's O(all-online-drivers-of-type), hard-capped at 500 (silently drops candidates in a dense market → corrupts match rate and breaches the <2s SLA). | Populate `location` geography on every location write (generated column / trigger / `ST_MakePoint`), add the GIST index, switch the hot path to the existing `match_and_claim_driver` RPC (already does `FOR UPDATE SKIP LOCKED`). |
| C3 | **Surge loop is not replica-safe.** `surge_recalculation_loop` has no leader lock or idempotency claim — every replica, every 2 min, recomputes and writes `service_areas.surge_multiplier` **and inserts a `surge_pricing` history row.** | `utils/surge_engine.py:225-308,356-372` | Surge is a **regulated, rider-facing price**; `surge_pricing` is the audit/justification record. R replicas → R duplicate audit rows per area per tick (corrupts analytics + the regulatory record) and R racing writes. | Wrap the tick in a Redis `SET NX EX` leader lock (the pattern the reconciliation/purge loops already use), or dedup the history insert with a per-(area, tick-window) idempotency key. |
| C4 | **`claim_stripe_event` dedup relies on error-string substring matching** (`str(e)` contains `"duplicate key"`), not the SQLSTATE `23505` the codebase already exposes via `pg_error_code()`. | `repositories/wallet_repo.py:281-282` | Two-sided failure: a message-format change makes replays 500 (Stripe retries forever → stuck); an unrelated error whose text contains "already exists" is misclassified as dedup → **a real payment is silently dropped, never processed.** | Branch on `pg_error_code(e) == "23505"`. |
| C5 | **OTP lockout keyed on phone only → targeted 24h login-DoS.** 5 wrong codes for a victim's number locks the legitimate user out for 24h; re-requesting an OTP doesn't clear it. | `routes/auth.py:149-165` | Unauthenticated attacker who knows a phone number can lock a user out of login. Common tradeoff, but should be an explicit decision (CLAUDE.md: ask before soft-handling security tradeoffs). | Add a per-IP gate and/or a stepped/shorter lockout; clear on successful OTP re-request from the owning device. |
| C6 | **Admin `/admin/auth/session` returns role/modules from an unverified token** — no `token_version`, `is_active`, or JTI-revocation check. | `routes/admin/auth.py:249-297` | A deactivated / logged-out-everywhere staff member still shows `authenticated:true` with full module claims until token exp. Not an authz bypass (`get_admin_user` is authoritative), but any UI/logic trusting this endpoint reflects a revoked session. | Route through `_verify_admin_payload` (or at least the DB `token_version`/`is_active` check). |

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**Overall:** PII hygiene in logs is genuinely good — phones masked to last-4, admin emails SHA-256 digested, **no raw GPS/phone/email/license logging found.** The 5xx detail sanitizer is a strong backstop. The issues are (a) a few leaks that bypass that backstop, and (b) log-and-continue on writes that CLAUDE.md forbids.

**User-experience leaks (raw errors reaching end users):**
- **ErrorBoundary renders `{error.name}: {error.message}` unconditionally in production** (`shared/components/ErrorBoundary.tsx:40-42`; only the stack is `__DEV__`-gated). Users see "Cannot read property 'x' of undefined". Same defect in `admin-dashboard/src/app/error.tsx:14`. → Show a generic message in prod; gate `error.message` behind `__DEV__`.
- **driver-app surfaces raw `err.message` in Alerts** (`become-driver.tsx:270,495`; `quests.tsx:99,108`) → "Network request timed out" shown to drivers. The rider app already uses the good pattern (`err.response?.data?.detail || 'friendly fallback'`) — driver app should match.
- **Store error strings set from raw `error.message`** on network failures (`walletStore.ts:86,126`, `rideStore.ts:455,689`), then surfaced via `error.message || fallback`. → Prefer `SpinrApiError.messageKey`/`actionHint`.
- **C1 above** (raw Stripe strings) is the backend equivalent.

**Admin-telemetry gaps (failures that won't reach an operator):**
- **Swallowed session-id write in verify-otp** — if `update_one(current_session_id)` raises, it logs error but *continues* and mints a token whose session was never persisted (breaks single-device revocation). Sibling new-user path correctly refuses. `routes/auth.py:558-566` → raise 503.
- **Admin single-logout silently no-ops the JTI blacklist on Redis failure** (`routes/admin/auth.py:531-548`, bare `except: pass`). Single-logout has no `token_version` bump, so the JTI denylist is the *only* revocation, and verification fails **open** → a "logout" that quietly did nothing. → Distinguish decode errors (ignore) from Redis write failure (log error).
- **`str(e)` interpolated into 500 `detail`** in `documents.py:313,945,959` — currently masked by the sanitizer, but the net is the *only* thing preventing a leak; change one to a 4xx and it leaks Supabase internals. → Static message + `logger.error(exc_info=True)`.
- **Middleware fallback handler logs without `exc_info`/`request_id`** (`core/middleware.py:594`); admin logout-all WS-kick failure logged at `warning` not `error` (`admin/auth.py:619`) → won't reach Sentry.

---

## 🐢 Performance Bottlenecks & Optimizations

Ordered by impact. C2 (dispatch scan) and C3 (surge replica-safety) above are also perf issues.

1. **`run_sync` 64-thread pool is the global ceiling for ALL DB I/O — and the GPS location-write firehose shares it.** Every online driver writes the durable `drivers` hot row on every GPS ping (~1/s) inline in `websocket.py:678`. A few hundred drivers can saturate the pool and **starve dispatch/fare/auth** — telemetry drowning revenue paths. Also bypasses `update_one` → stale by-id driver cache + index churn. (`repositories/_base.py:136-137,196`) → Write live positions to Redis only; persist to `drivers` at low frequency / on state change. Longer term migrate hot paths to **asyncpg** so DB concurrency isn't thread-bound.
2. **Same `service_areas` row re-fetched 4–6× per single dispatch attempt** (`rides.py` config/subscription/quota/cascade blocks: `:779,784,873,907,912`; mirrored in `dispatch_service.py:330-333`). 4–6 serialized ~40–80ms round-trips on the latency-critical path. → Fetch area+parent once at the top of `match_driver_to_ride`, thread it through.
3. **Per-attempt dispatch retries re-run the entire 500-row scan + filters every 10s** for up to ~5 min (`rides.py:1031`) — multiplies #C2's cost ~30× for exactly the no-supply rides. → Event-driven: subscribe the searching ride to driver-go-online events for its area; at minimum backoff + skip filters that can't have changed.
4. **Surge supply count is another full driver-table scan per area, capped at 5000**, and `get_surge_status` does it **synchronously in a request** on admin dashboard load (`surge_engine.py:140-201,311-353`). The 5000 cap silently over-prices. → Enable the existing `SURGE_SPATIAL_COUNT` RPC (migration 170); serve `get_surge_status` from last-computed rows.
5. **WS `broadcast()` fans out sequentially with a 2s per-socket timeout** (`socket_manager.py:315-336,409-427`) → tail latency on the <100ms fan-out SLA. → `asyncio.gather` the sends (each already timeout-wrapped); add the noted viewport/bbox filter for admin location fan-out.
6. **No read-replica strategy** — one Supabase client for OLTP + analytics + surge (`_base.py:37-39`). Heavy admin/analytics scans contend with dispatch/payments. → Route read-only analytics + surge counts to a read replica.
7. **`_admin_loc_last` in-process map is never evicted** (`socket_manager.py:370`) — slow unbounded growth keyed by every driver_id ever seen. → TTL-evict.
8. **16+ background loops fire on aligned intervals** sharing the 64-thread pool → periodic pressure spikes. → Apply the surge loop's ±10% jitter pattern to all fixed-interval loops; add explicit limits + pagination to unbounded sweeps.

_Verified non-issues:_ admin list endpoints already batch via `.in_()` (no N+1); dispatch offer-skip/presence already uses a single Redis `MGET`; GPS breadcrumbs are Redis/DB-backed (no in-process memory leak); the ride-acceptance and driver-claim atomic guards are correct.

---

## 💡 Tech Stack & Architecture Recommendations

The stack (FastAPI + Supabase + Stripe + Redis + Firebase/FCM + Expo + Next.js, Fly-primary/Railway-standby) is sound and appropriate. The gaps are in the operational tier that every mature platform has at this stage:

| Gap | Why it matters | Concrete tool / approach |
|---|---|---|
| **No staging environment** (deploys go `main`→prod on both Fly+Railway) | Root blocker — also blocks load testing, DAST, and safe migration rehearsal simultaneously. | Fly staging app + **Supabase branching** (throwaway branch DB w/ synthetic data), gated on a `staging` branch. **Highest-leverage single item.** |
| **Synchronous DB driver in a thread pool** (`supabase-py` via `run_sync`) | Caps throughput on the thread pool, not the event loop; no transaction pooling. | Front Supabase with **Supavisor/PgBouncer** transaction pooling; migrate hot paths (location writes, dispatch claims) to **asyncpg**. |
| **No distributed tracing** (only `X-Request-ID`) | Can't debug P95 breaches across 25 routers + 16 loops + Supabase/Stripe/Twilio. | **OpenTelemetry** (FastAPI + httpx + redis auto-instrumentation) → **Grafana Tempo / Honeycomb** (both have Canadian-region options for PIPEDA residency). |
| **Metrics are per-replica in-process, no aggregation** (`utils/metrics.py`) | With horizontal scaling, dashboards miss replicas; no external SLO probing. | Keep the shim; add Grafana Cloud / Fly metrics scraping each replica + **Checkly / Grafana Synthetic Monitoring** for external SLO alerting tied to the CLAUDE.md SLA table. |
| **No feature-flag / kill-switch framework** | `app_settings` covers config, but no instant kill switch for dispatch/surge/payments or cohort rollout. | Self-hosted **Flipt** or **Unleash** behind **OpenFeature** (self-hosting keeps flag data in-region vs LaunchDarkly SaaS). |
| **No DAST** (SAST is strong) | Nothing exercises the running app. | **OWASP ZAP** baseline GitHub Action vs staging on a schedule → SARIF into the existing code-scanning UI. |
| **JS license scan missing** (Python covered by G7) | Largest dependency surface (rider/driver/admin/shared) unchecked for GPL/AGPL. | `license-checker-rseidelsohn` as a G8 gate. |
| **No CODEOWNERS** | Money/auth/migration changes can merge on a drive-by approval. | `.github/CODEOWNERS` routing `payments*`/`fare*`/`migrations/` to required reviewers. |

---

## 🛠️ Maintainability & Code Smells

- **God files:** `drivers.py` (8,468 lines), `rides.py` (6,138). The `DispatchService` extraction is real but thin — the *actual* live dispatch pipeline still lives inline in `rides.py:638-1057`, **duplicating** the subscription/quota/presence filters that also exist in `DispatchService.find_candidate_drivers` (the code comments literally say "mirror"). This duplication is a **correctness hazard** (fix applied to one copy, not the other) more than a perf one. → Make the route call `DispatchService` for candidate selection so there is one implementation.
- **`useDriverDashboard` is a 1,565-line mega-hook on the latency-critical driver screen** — owns WS lifecycle, foreground/background, location watch + batching, animations, notifications, leaning on many `any`-typed refs and generation-counter guards (comments describe repeatedly fighting teardown-on-re-render bugs). → Split into `useDriverSocket` / `useDriverLocation` / `useOfferNotifications`; highest-risk file for regressions.
- **Type-safety "sweep" is incomplete.** Typed `: any` (excl. tests): admin **319**, rider **123**, driver **104** (shared is clean at 6). The riskiest are on the real-time path — **WS message handlers take `data: any`** (`useRiderSocket.ts:71`, `useDriverDashboard.ts:719`) driving ride-state transitions from untyped server payloads. → Define discriminated-union types for inbound WS messages + the offer payload.
- **Doc drift:** `ARCHITECTURE.md` still describes a **Railway-only** backend and a `develop→main` two-branch flow, while the real system is Fly-primary/Railway-standby, `main`-only. Will mislead every new engineer. → Update or delete.
- **Two dead offline-queue implementations** (see C-Testing / offline below) — remove whichever you don't keep.

---

## 🧪 Testing & QA (Missing Edge Cases)

**Test *authoring* is broad and good** — ~230 backend pytest files; ride state machine (14 tests + offer-timeout + atomic-cancel), Stripe webhooks (16+ event types), fare/surge/corporate/promo/refund all have dedicated files; the `_STALE_TEST_CLASSES` registry is clean/empty. The gap is almost entirely **enforcement and environments**, not missing tests.

1. **No per-module coverage floors — the #1 gap (ACTION_ITEMS A1).** `pytest.ini` enforces a single global 60%; CLAUDE.md mandates ≥90% payments/fare and ≥80% rides/dispatch. **A payments regression dropping fare coverage to 65% ships green.** → Add `coverage report --include=... --fail-under=90` per protected path (one line each), or Codecov `components:` targets. Ratchet, don't big-bang.
2. **rider-app & driver-app E2E suites exist but never run** — only `admin-dashboard` runs `test:e2e` in CI. The highest-risk flows (booking, OTP-at-pickup, payout) are only unit-tested. → Add rider/driver E2E jobs; for RN, **Maestro** (already in `.maestro/`) or **Detox** fits device flows better than Playwright.
3. **Marketplace load test never executed** — `loadtest/locustfile.py` is solid (real dispatch, 409 accept-race, WS pings, SLA gates) but blocked on no staging env, so the CLAUDE.md SLA table is asserted by nothing in CI.
4. **No accessibility testing** despite WCAG 2.1 AA regulatory mandate — no `axe-core` anywhere. → `@axe-core/playwright` in the E2E suites.
5. **Offline resilience is non-functional (real gap vs Uber/Lyft).** Two offline-queue systems exist and **neither queues anything** — `shared/api/offlineQueue.ts` is never imported; `rideStore.syncOfflineRequests` only ever *writes back* a drained queue, nothing enqueues `create_ride`/`cancel`/`tip`/`emergency`. Despite "Sync Failed"/offline-banner UI, there's zero offline booking/cancel/SOS resilience. → Wire `enqueueRequest` into the network-error path; **and add idempotency keys at enqueue time** — replay currently omits the `Idempotency-Key` the live path sends (`rideStore.ts:561` vs `:673-675`), so any future replay would **double-book/double-charge.**

**Smaller correctness edges:** rider WS reports `connected` before auth is confirmed (`useRiderSocket.ts:264`; driver app does it right on `auth_success`); `_toFiniteCoord` accepts latitudes up to ±180 on the offer path (`useDriverDashboard.ts:64-69`) — should clamp to ±90.

---

## 📈 Manager's Verdict — Overall Code Health

**Grade: B+ / "Beta-ready, not yet dense-market-launch-ready."**

This team has done the hard, unglamorous work most startups skip: a correct JWT trust model, fail-closed OTP/secrets, Stripe idempotency, PIPEDA-grade PII-in-logs discipline, migration-safety CI, a real security SAST pipeline, and an honest, well-maintained `ACTION_ITEMS.md`/sprint log. Readability and documentation are top-decile. **The codebase is trustworthy.**

What separates it from an Uber/Lyft-scale launch is not correctness of the happy path but **behavior under load and at the edges**:
- **Dispatch and surge both do full-table scans** where the incumbents use geospatial cell indexes — the PostGIS index literally exists but is dead code (C2). This is the one architectural item that will visibly break (SLA + match rate) in a dense market and should be funded first.
- **A synchronous DB driver in a bounded thread pool, shared with a GPS-write firehose** (P1) is a self-inflicted priority inversion waiting for its first busy Friday night.
- **A short list of genuine leak/DoS bugs** (C1, C4, C5, ErrorBoundary) — small diffs, real user/security impact.
- **Offline resilience is theater** — the UI promises it, the code doesn't deliver it, and the latent replay path would double-charge.
- **Operational tooling** (staging, per-module coverage gates, DAST, tracing, kill switches) is the difference between "we find out from a user" and "we find out from an alert."

**Risk if shipped as-is to a real market:** dispatch latency/match-rate degradation as driver density rises; occasional revenue-path starvation under GPS load; a handful of embarrassing raw-error leaks; and a blind spot during the first incident (no tracing, no staging to repro, no kill switch). None are fatal; all are known and tractable.

---

## Recommended Plan (phased, mapped to existing backlog)

**Phase 0 — Small, high-value bug fixes (days, low risk):**
- C1 raw Stripe leak → generic rider message.
- C4 `claim_stripe_event` → SQLSTATE `23505` (prevents silent dropped payments).
- ErrorBoundary + driver-app Alerts → generic messages in prod.
- C6 admin `/session` → verify token_version/is_active; auth.py session-id write → raise 503.
- Delete/keep one offline queue; update stale `ARCHITECTURE.md`.

**Phase 1 — The dispatch/DB scaling ceiling (the flagship, 1–2 sprints):**
- C2: populate PostGIS `location` on every write + GIST index + switch hot path to `match_and_claim_driver` RPC. (Ties out ACTION_ITEMS D1.)
- P1: move live location writes to Redis; persist to `drivers` on state change only; begin asyncpg migration for hot paths.
- P2/#9: fetch `service_areas` once per dispatch; collapse the duplicate filter copies into `DispatchService`.
- C3: Redis leader lock on the surge loop (stops duplicate regulated-price audit rows today).

**Phase 2 — Environments & enforcement (unblocks everything, 1 sprint):**
- E1 staging env (Fly + Supabase branching) — highest leverage.
- A1 per-module coverage gates (payments/fare 90%, rides/dispatch 80%).
- A2 post-deploy functional smoke; run rider/driver E2E + the Locust load test against staging.
- C5 OTP-lockout DoS decision (per-IP gate).

**Phase 3 — Operational maturity (post-beta):**
- OpenTelemetry tracing; cross-replica metrics + external synthetic SLO alerting (E4).
- Feature flags / kill switches (E5, Flipt/Unleash + OpenFeature).
- OWASP ZAP DAST (E6); JS license scan (E10); CODEOWNERS (E8); a11y in CI (E11).
- asyncpg + read-replica for analytics/surge; split the god files & the driver mega-hook.
