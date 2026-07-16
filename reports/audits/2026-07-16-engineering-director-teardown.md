# Spinr — Engineering Director Teardown & Remediation Plan

**Date:** 2026-07-16
**Reviewer role:** Engineering Director / Chief Architect (read-only)
**Scope:** Full-stack review across `backend/`, `admin-dashboard/`, `rider-app/`, `driver-app/`, `shared/`, CI/CD, and infra.
**Method:** Five parallel specialist passes (security, money-paths, backend performance/error-handling, mobile, admin/CI/testing) against the current committed state of `main`/`claude/zealous-cori-gpuck8`. This is an analytical teardown — **no code was modified.**

> **Headline:** Spinr is a genuinely mature, defensively-built platform — well above the median pre-launch startup. The core money, auth, dispatch, and WebSocket paths are correct and hardened, often exceeding the bar set in `CLAUDE.md`. The residual risk is concentrated in **six areas**: one live XSS vector, event-loop-blocking Stripe calls, a dispatch-path hard Redis dependency, a mobile 401-interceptor bug that destroys sessions on flaky networks, a documented-but-clamped surge override, and operational gaps (no enforced money-path coverage, no post-deploy smoke, no external monitoring). None are architectural; all are fixable inside a normal sprint.

---

## 🚨 Critical Issues & Security Flaws

| # | Sev | Area | File | Issue | Impact if unaddressed |
|---|-----|------|------|-------|-----------------------|
| C1 | **P1** | Admin XSS | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx:93` | `toText()` strips HTML by assigning an untrusted inbound email body to `document.createElement("div").innerHTML`. Detached nodes don't run `<script>`, but `<img src=x onerror=…>` **fires on parse**. | A crafted support email runs JS inside an authenticated admin session — can drive refunds, driver approvals, PII reads. Token is HttpOnly, but the *session* acts. |
| C2 | **P1** | Input validation | `backend/routes/admin/drivers.py:1032` | `admin_update_driver(updates: Dict[str, Any])` — raw untyped dict; the allowlist checks key membership only, not value type. | A malformed type on a compliance field (e.g. `license_expiry_date`) corrupts the `go_online` document-expiry gate, or forces a raw PostgREST 500 that leaks column internals. |
| C3 | **P1** | Dispatch availability | `backend/routes/rides/matching.py:325` | Offer-skip `_redis_mget(...)` is the **one** unguarded Redis call on the dispatch hot path (presence filter and cascade are fail-open). | A 2-minute Redis blip raises on every dispatch attempt → retries exhaust → stuck-ride sweeper cancels rides as "no drivers found." A **total dispatch outage caused by a cache.** |
| C4 | **P1** | Session integrity | `shared/api/client.ts:784` → `authStore.ts:586` | `refreshTokens()` correctly keeps the session on a transient 5xx/429/timeout, but `handleApiError` then falls through and calls `logout()`, **deleting the refresh token from SecureStore**. | On a Railway cold-start 503 or flaky network, a rider/driver with a *valid* refresh token is hard-logged-out and cannot recover without re-OTP. Contradicts the app's own retention design. |
| C5 | **P2** | Middleware auth | `admin-dashboard/src/middleware.ts:117` | `isTokenValid()` decodes the admin JWT and checks `exp` only — **no signature verification** (Edge can't reach `JWT_SECRET`). | Anyone can self-mint `{"exp": <future>}` and pass the auth redirect; combined with C6, reach every page shell. Defense-in-depth collapses to the API layer alone. |
| C6 | **P2** | Access control | `admin-dashboard/src/middleware.ts:94` | IP allowlist uses `clientIp.startsWith(entry)` — `"192.168.1"` also admits `192.168.100.x`; trusts the first client-appendable `x-forwarded-for` hop. | The allowlist is porous by prefix and spoofable by header. |
| C7 | **P2** | Webhook forgery | `backend/routes/webhooks.py:1783` (Twilio), `:1698` (SES/SNS) | Both **skip signature verification and log a warning** when the secret is unset in `app_settings`; no fail-fast prod guard was found (unlike `JWT_SECRET`/`FIREBASE_*`). | If blank in prod, an attacker forges SNS bounce/complaint events (mail-suppression DoS) or Twilio STOP webhooks (silent SMS opt-out). |

**Verified strong (do not regress):** JWT trust model (rider/driver role always re-read from DB, admin claims gated on `aud`); OTP (SHA-256 at rest, constant-time compare, 5/hr → 24h lockout fail-closed, separate send-cap, dev-bypass hard-gated on `ENV`); refresh-token rotation with replay block; Stripe webhook idempotency (`claim_stripe_event` before every side effect + unclaim-on-transient); centralized admin RBAC at the router level; money RPCs retrofitted to `SECURITY DEFINER` + pinned `search_path` (migration 203); WS auth-before-message + per-user rate limit + 64 KB cap; SOS uses `allow_expired` token and never logs Twilio error text.

---

## 🛡️ Error Handling & Telemetry (User experience vs. Admin logging)

**The discipline here is a codified convention with a regression test (`test_error_response_sanitisation.py`) — the findings below are stragglers that bypass it, not the norm.**

**Raw error text leaking to end users:**
- `backend/documents.py:323,955,969` — `detail=f"Could not save file: {e}"` / `"Storage upload failed: {e}"` on **driver-facing** upload paths; Supabase Storage errors can embed bucket/URL detail. **Fix:** log with `exc_info`, return a generic 502/503.
- `backend/routes/admin/drivers.py:1850`, `driver_import.py:67,90` — `detail=str(e)` (admin-only, lower risk).
- Mobile: `rider-app/app/promotions.tsx:54` renders `err.response?.data?.detail` raw — for a 422 that `detail` is an **array**, rendering as `[object Object]`. Plus ~6 raw `err.message` toasts (`manage-cards.tsx:106`, `wallet.tsx:111,118`, `ride-options.tsx:493`, `documents.tsx:249`) bypass `getApiErrorMessage`. **Fix:** route all through `getApiErrorMessage`.

**PII in logs (PIPEDA — `CLAUDE.md` forbids raw GPS/phone/email/name):**
- `backend/routes/rides/estimates.py:167,195,216` — logs exact `pickup_lat/lng` on service-area-reject (and feeds the loguru→Sentry bridge). `matching.py:219` already strips coords from the same class of log; estimates.py didn't get the fix.
- `shared/api/client.ts:645` — the `_redactGpsUrl` regex misses `pickup_lat`/`pickup_lng`, so `/promo/available?...pickup_lat=52.13…` reaches device console **and** the persisted `@spinr/api-error-log` AsyncStorage trail.
- `backend/routes/wallet.py:170` sends rider email + legal name to Stripe while `payments.py:128` deliberately withholds both — inconsistent customer-create PII policy.

**Telemetry blind spots (a failure that pages nobody):**
- **Loop watchdog is structurally blind** (`core/lifespan.py:415`, `loop_monitor.py:80`): `push_retry`, `subscription_expiry`, `t4a_annual_job` are registered but never call `record_heartbeat` → permanently `never_ticked`, which is explicitly *not* flagged unhealthy. `safety_checkin` records a mismatched name (`safety_checkin_loop`) and isn't registered → **a dead rider-safety escalation loop alerts no one.** Six more loops (`preauth_capture`, `referral_payout`, `reconciliation`, …) are neither registered nor instrumented.
- Fast backstop loops (`stuck_ride_sweeper` 60s, `offer_expiry_reaper` 10s) inherit the **2-hour** default staleness threshold (`loop_monitor.py:23`) — a deadlocked 10s reaper strands driver claims for up to 2h post-deploy with no alert.
- `loop_alert.py:30` throttles per-replica (in-process `_last_alerted`) → every replica pages for the same stale loop; and it's a silent no-op when `ALERT_WEBHOOK_URL` is unset, with no Sentry fallback.

**Verified strong:** Sentry PII scrubbing + `send_default_pii=False` + loguru bridge with tag promotion (79 `extra={...}` sites); loud production error when `SENTRY_DSN` missing; deliberate, documented Redis degradation policy (fail-open caches, fail-closed OTP).

---

## 🐢 Performance Bottlenecks & Optimizations

**P1 — event-loop hazards (these stall the *whole worker*, not just the caller):**
1. **Synchronous Stripe SDK calls on the event loop** — `routes/payments.py:134,501,741,745,758,811`, `wallet.py:169`, `corporate_accounts.py:337`, `disputes.py:238`. The core charge path (`utils/stripe_charge.py`) is correctly `asyncio.to_thread`-wrapped; these card/setup/refund calls are not. One rider adding a card during a Stripe slowdown freezes WS fan-out (<100ms SLA) and dispatch offers (<2s SLA) behind it. **Fix:** wrap each in `asyncio.to_thread`, mirroring the adjacent charge path.
2. **slowapi rate-limit checks are blocking Redis round-trips** (`utils/rate_limiter.py:124`) — the sync `limits` storage does a network hop *inside* the async path on every decorated endpoint (incl. 120/min ride-read polling). 3–20ms of loop stall per request against a managed Redis. **Fix:** move to `limits.aio` / `redis.asyncio`.
3. **No deadline/queue-depth on the 64-thread DB pool** (`repositories/_base.py:136`) — the circuit breaker counts exceptions only; a Supabase that answers slowly (not erroring) pins all 64 threads and every request (auth, 200ms SLA) queues invisibly. **Fix:** wrap the executor await in `asyncio.wait_for` off the existing `X-Deadline-Ms` contextvar; export `_work_queue.qsize()`.

**P2 — throughput / correctness under load:**
4. **Surge engine re-fetches the entire fleet per area per 2-min tick** (`surge_engine.py:162`) — `get_rows("drivers", …, limit=5000)` with **no geo filter**, polygon-matched in Python, once per area. 10 areas × 2000 drivers = 20k-row fetches per tick competing with dispatch for DB threads. The fix (`drivers_available_in_polygon` RPC, migration 170) exists but is **off by default** (`SURGE_SPATIAL_COUNT`). **Fix:** rehearse in staging, enable; at minimum add a bounding-box prefilter.
5. **Generic write helpers retry non-idempotent writes under the read policy** (`repositories/_base.py:610,671,735`) — `insert_one`/`update_one`/`rpc` default to `retry_policy="read"` (2 retries incl. on timeout). An INSERT whose response times out *after commit* retries → duplicate row (notifications, breadcrumbs, surge history, ride_offers). The `write`/`idempotent_write` machinery exists but is effectively dead code. **Fix:** default inserts/rpc to `write`, filtered updates to `idempotent_write`.
6. **Scheduled-ride dispatcher runs every ~90–120s, not 60s** (`scheduled_rides.py:257`) — the leader lock TTL (90s) exceeds the tick interval (60±6s) and is never released, so 1–2 subsequent ticks are skipped. Rides can dispatch ~2 min late and jump the 10-min reminder window. **Fix:** `ttl=55` or delete-after-tick; dispatch per-ride work concurrently.
7. **Full `service_areas` read (500 rows incl. polygon JSON) per fare estimate** (`estimates.py:152`) — static config re-fetched on the <300ms path, re-scanned per stop. **Fix:** 30–60s cache, same pattern as `get_app_settings`.
8. **Seven stacked `BaseHTTPMiddleware` layers** (`core/middleware.py:628`) — each spawns an anyio task-group per request; low-single-digit ms + an unverified `jwt.decode` per request, material against the 150ms location-write SLA. **Fix:** collapse header/redirect/deadline layers into pure-ASGI middleware.

**Mobile perf:** duplicate GPS breadcrumbs after a WS outage (`useDriverDashboard.ts:670` writes to both the WS batch and REST buffer, inflating `gps_points_count` toward the settlement cap); driver-earnings re-summed as floats in two places (see money section). Location cadence, render throttling, and 500-point buffer caps are otherwise well-tuned.

**Verified strong:** dispatch hot path (geo bounding box + partial index, PII-excluded projection, batched MGET/IN, 1.2s Distance-Matrix cap inside the 2s budget, post-claim re-validation, escalating-backoff recovery shell); `run_sync` transport hardening (H2 GOAWAY classification, jittered backoff, deadline-aware retry, half-open circuit breaker excluding app-level 23505); WS durable-vs-ephemeral split so 1Hz pings can't evict ride events.

---

## 💡 Tech Stack & Architecture Recommendations

**Current stack (factual inventory):**

| Component | Version | Notes |
|---|---|---|
| Backend | Python 3.12.9 (SHA256-digest-pinned image), FastAPI 0.136.1, Pydantic 2.13 | uvicorn multi-worker, non-root container, HEALTHCHECK |
| Data | Supabase (postgrest 2.29) + Redis (asyncio ≥5, in-proc dict fallback) | all durable state in Supabase; service-role from backend |
| Payments / comms | Stripe 15.1, Twilio ≥9.10, Firebase-admin 7.4 | keys in `app_settings` table (rotatable) |
| Dep pinning | 3-tier: `.in` → compiled `.txt` → `-locked.txt` (`--require-hashes`) | **best-in-class**; CI verifies hash count + drift |
| Admin | Next.js 16.2, React 19.2, Zustand 5, Recharts 3.8, maplibre-gl 5.24, Tailwind 4, Sentry 10.51 | Vitest + Playwright (+axe) in CI |
| Mobile | Expo SDK ~55.0.26, RN 0.85.2, React 19.2, RN-Firebase 24, LogRocket | (`CLAUDE.md` still says SDK 54 — stale) |
| Geo | Hybrid: Python haversine + bbox SQL prefilter for **dispatch**; PostGIS `ST_Covers` + GIST index for **surge** (behind `SURGE_SPATIAL_COUNT`, default OFF) | no PostGIS on the dispatch hot path yet |
| Task queue | **None** — 16 replay-safe asyncio loops in `core/lifespan.py` | deliberate; no Celery/ARQ |
| APM / tracing | **None** — Sentry + custom token-gated `/metrics` | no OpenTelemetry |
| Feature flags | Env vars + `app_settings` | no flag service / kill switches |
| Infra | Fly.io yyz primary (2× shared-cpu-1x) + Railway standby (**1 replica**), Cloudflare CNAME failover, Vercel admin | |

**Recommended additions (the "why," ranked):**
1. **Async storage for slowapi + wrap remaining Stripe calls** — closes the event-loop-blocking class entirely. Highest ROI; small diffs.
2. **OpenTelemetry tracing (D2 in the backlog)** — the platform's marquee SLA (dispatch offer → driver phone < 2s) spans REST → DB → Redis → WS → FCM and currently **can't be decomposed**. Sentry performance + OTel spans would turn "dispatch is slow" into "the Distance-Matrix call is the P95." Adopt when multi-replica latency debugging first hurts, not before.
3. **Feature-flag / kill-switch layer (E5)** — `app_settings` holds config but there are no documented switches for surge, scheduled dispatch, promo redemption, or corporate billing. A misbehaving subsystem currently requires a deploy to disable. A boolean-per-subsystem checked at the top of each loop is a few hours' work and buys seconds-to-mitigate.
4. **Staging environment (E1)** — deploys go `main` → prod (Fly + Railway) with only a partial Railway test backend. This blocks safe migration rehearsal (migration 170 literally gates its own flag on "a staging rehearsal"), load testing (E2), and DAST (E6). This is the single biggest operational-maturity gap.
5. **Enable PostGIS on the dispatch/surge count path** — the RPC already exists; it removes the per-tick full-fleet Python scan.
6. **Standby parity** — Railway is a single replica with a `|| true`-swallowed rollback; a Cloudflare failover lands 100% of traffic on one shared-cpu box that may itself be a failed deploy. Run 2 replicas; surface rollback failures.

---

## 🛠️ Maintainability & Code Smells

- **`CLAUDE.md` is materially stale** — says highest migration `144` / next slot `145`; actual is **231** (290 files). Says Expo SDK 54; apps are on 55. The file self-flags staleness, but an agent skipping the re-verify step picks a colliding migration number. **Refresh the migration/Expo/next-slot anchors.**
- **Stale backlog masking done work** — `ACTION_ITEMS.md` B2 (disputes RLS + rounding) is marked `[ ]` open but migration 142 + the `dollars_to_cents` switch already shipped it. Stale "still broken" entries risk a re-fix or an auditor waving through a regression. Close B2 with a pointer to migration 142; track only the residual `user_name` `DROP COLUMN` follow-up.
- **Dead/duplicated code:** two non-functional offline-queue implementations (`shared/api/offlineQueue.ts` has zero call sites; `rideStore.syncOfflineRequests` reads an AsyncStorage key nothing writes) — and if wired, the rideStore path would replay `create_ride` with **no idempotency key** (double-booking risk). `shared/api/cachedClient.ts` is broken-by-design (reads a SecureStore token that's deliberately deleted → every native request unauthenticated) yet still exported. **Consolidate into one wired, TTL'd, idempotent queue; delete `cachedClient.ts`.**
- **RN duplication drifting** — near-identical `CancelReasonSheet`, `BrandSplash`, `CarMarker` (in both `shared/` and `driver-app/`), `AiAuroraBackground` (in `shared/` and `rider-app/`), plus parallel `otp/login/profile-setup` screens. Consolidate into `shared/` before behavioural drift.
- **`any` sweep unfinished** — 212 `: any` + 75 files with `as any` outside tests, concentrated exactly on the WS message handlers that would benefit most from `shared/types/api/wsEvents.ts`.
- **Repo-root hygiene** — `test_admin_endpoints.py`, `validation_output.txt`, legacy `frontend/` (ci.yml marks it deletable) sit at root; archive per the `CLAUDE.md` stale-directory note. Admin: no `global-error.tsx` (root-layout throws unguarded); ESLint floor is a generous `--max-warnings 600`.

---

## 🧪 Testing & QA (Missing Edge Cases)

- **RLS policies have no allow/deny tests** despite the `CLAUDE.md` mandate ("every auth/RLS policy, both paths"). The 330 backend test files mock Supabase, so RLS is *untestable* there, and no integration tier exercises the anon key against real policies. Since the service role bypasses RLS by design, **the only thing protecting user data from the anon key is a policy set no test has ever run.** Stand up the throwaway-schema integration tier already named in `CLAUDE.md` with per-table allow/deny assertions. **Highest-value test gap.**
- **Per-module money-path coverage floors not enforced (A1)** — `pytest.ini` is a global `--cov-fail-under=60`; the 90% (payments/fare) and 80% (rides/dispatch) floors from `CLAUDE.md` are aspirational. Money-path branches can sit uncovered while the global number stays green. Add `coverage report --fail-under=90 --include=routes/payments.py,services/fare_service.py` guardrail steps; ratchet from measured actuals.
- **Load tests exist but never run** — `loadtest/locustfile.py` is wired to no workflow; SLAs are enforced only by `perf_baseline.py` micro-benchmarks. Blocked on staging (E1).
- **SOS regression coverage is good** (4-state UX + `isSosUrl` interceptor exemption verified), but the **latency** path (finding below) has no test asserting a bounded time-to-feedback.

**Present and healthy:** `test_ride_state_machine.py`, fare-branch tests (`test_fares.py`, `test_fare_split.py`, `test_e16_surge_boundary.py`, corporate surge-bypass), Stripe webhook-type tests, dispatch (cascade/WAV/metrics/perf); rider 32 / driver 31 Jest files; admin Vitest + 3 Playwright specs with an axe fixture.

---

## 💰 Money-Path Findings (dedicated pass — no blockers, four warnings)

1. **Manual surge override is documented, audited — and silently clamped to 2.5×.** `admin/service_areas.py:203,517` fully implements the "manual override up to 10× with written justification" flow (validates, requires `surge_justification`, writes an audit row), but every pricing path (`fare_service.py:399`, `fares.py:235`, `features.py:993`) does `min(surge, SURGE_CAP)` **regardless of `surge_source`**. Ops sets 4.0× for a festival with SGI notified → the write succeeds and audits → riders still price at 2.5× with no signal it was clipped. **The code and `CLAUDE.md` disagree.** Reconcile with product/legal: either honor `surge_source == 'manual'` above the cap, or remove >2.5× admin support and document the cap as absolute.
2. **Driver-earnings display re-sums Decimals as floats** (`routes/rides/queries.py:539`, duplicated in `drivers/ride_reads.py:361`) — pulls already-quantized snapshot fields, converts to float, re-sums with banker's `round()` instead of trusting `driver_earnings_snapshot["total"]` (built as an exact HALF_UP Decimal precisely for the T4A document). Off-by-a-cent divergence at rounding boundaries, in **two** files → rider-history and driver-history can disagree for the same ride. **Fix:** use `des.get("total")` directly.
3. **Corporate fallback passes floats into money RPCs** (`payment_service.py:423,432,437,446`) — `_f()` (a JSON-response-boundary helper by its own docstring) converts a `_round()`ed Decimal to float before `apply_rollback/apply_grant/apply_adjustment`, which then `str()` it. Safe by luck today; a drift landmine. Pass the Decimal directly.
4. **Dead `/wallet/pay` validates against `total_fare`, not `grand_total`** (`wallet.py:249`) — inconsistent with every other settlement path (all use `grand_total` incl. area fees + tax). The RPC gates correctly so it can't underpay, and the endpoint is currently unreferenced — but a landmine if rewired. Align the route guard.

**Verified clean:** Stripe idempotency (deterministic ride/amount/attempt-scoped keys, never random UUIDs); cents-at-boundary via `dollars_to_cents` HALF_UP; refunds (real Stripe API + deterministic key before DB flip, driver pay preserved); surge before tax, never on base/booking/airport fees; corporate + scheduled rides force `surge=1.0` after token resolution; all corporate deltas via row-locked `SECURITY DEFINER` RPC; fare-split remainder-to-requester; GST/PST as independent quantized line items; settlement `payment_status` conditional claims + webhook underpay guards. **The disputes.py `int()` rounding bug named in the brief is already fixed.**

---

## 📈 Manager's Verdict (Overall code health)

**Grade: B+ / strong pre-launch.** This codebase is built by people who understand ride-share failure modes. The invariants that separate a real platform from a demo — atomic dispatch claims, replay-safe background loops, Decimal-only money, HttpOnly tokens with rotation, Stripe idempotency, WS event-versioning, PII discipline, `SECURITY DEFINER` money functions — are present and tested. The dependency-pinning and CI-gate posture is **better than most Series-B companies.** Nothing found is architectural; there is no rewrite here.

**What holds it back from an A** is a small number of sharp edges plus operational immaturity:
- **Sharp edges (fix this sprint):** the admin XSS (C1), the four event-loop-blocking classes (Stripe sync calls, slowapi sync Redis, DB-pool deadline, dispatch Redis dependency), and the mobile 401→hard-logout bug (C4) — each is a small diff with outsized blast radius.
- **Operational gaps (fix before public launch):** no staging, no external/synthetic monitoring, no feature-flag kill switches, no enforced money-path coverage, no post-deploy smoke, single-replica standby, untested RLS. These are exactly the things a mature platform has and the reason "it works in the demo" and "it survives a bad Tuesday at scale" are different claims.

### Where Spinr stands vs. Uber / Lyft (honest comparison)
- **Correctness & compliance:** *ahead of where the incumbents were at equivalent stage.* PIPEDA/SGI/Saskatchewan-Transportation-Act obligations are baked into the schema and retention jobs; the 0%-commission model and receipt-line-item transparency are cleaner than the incumbents' fee stacks. This is a genuine differentiator, not just parity.
- **Reliability engineering:** *behind.* Uber/Lyft run staging + canary + synthetic probes + distributed tracing + regional multi-AZ as table stakes. Spinr is a single horizontally-scalable process with a 1-replica standby, no staging, no external monitoring, and asyncio loops instead of a durable task queue. That's a reasonable *pre-launch* posture, but every item in the "operational gaps" list is something the incumbents would consider non-negotiable before a public marketplace.
- **Observability:** *behind.* Sentry + a custom `/metrics` endpoint is solid, but the absence of tracing means the flagship dispatch-latency SLA can't be decomposed across the very hops it spans. This is the first thing to add the moment traffic makes latency debugging painful.
- **Product surface:** *deliberately narrower and better-scoped.* The backlog (destination mode, driver heatmap, forced-upgrade gate) shows the team knows the parity gaps and is choosing sequence, not missing them.

**Recommended sequence (a realistic sprint plan):**

| Phase | Items | Rationale |
|---|---|---|
| **Sprint 1 — sharp edges** | C1 XSS (one-line `DOMParser`), C2 Pydantic model, C3 guard dispatch MGET, C4 soft/hard-logout split, wrap sync Stripe + slowapi async storage, estimates.py GPS-log + client.ts redaction regex | All small diffs, all high blast-radius. Add a regression test to each. |
| **Sprint 2 — money & telemetry** | Surge-override reconciliation (product/legal call first), driver-earnings float re-sum, corporate-RPC float, loop-watchdog heartbeats + safety-checkin name, per-loop staleness thresholds | Correctness + "a dead loop pages someone." |
| **Sprint 3 — operational maturity** | Staging env (E1), post-deploy smoke (A2 — script already exists), money-path coverage floors (A1), external synthetic monitor (E4), feature-flag kill switches (E5), RLS integration tests, Railway 2-replica + unblock pip-audit | The "survives a bad Tuesday" list. Staging unblocks load testing and DAST. |
| **Backlog** | OpenTelemetry (D2), PostGIS on dispatch (D1), offline-queue consolidation, `any` sweep, `CLAUDE.md` refresh, CODEOWNERS (E8) | Adopt as the pain that justifies each one arrives. |

**Bottom line:** ship-ready on correctness, not yet on operations. Close the seven sharp edges in Sprint 1 and stand up staging + monitoring + kill switches before opening the doors to the public, and this is an A-grade launch.

---

*Generated by a five-agent parallel review (security, money-paths, backend perf/error-handling, mobile, admin/CI/testing). Read-only — no source files were modified. Findings reflect committed state as of 2026-07-16.*
