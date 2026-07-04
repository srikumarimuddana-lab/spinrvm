# Spinr Platform — Comprehensive Engineering Review & Remediation Plan

**Date:** 2026-07-04 · **Type:** Read-only architectural teardown (no code modified)
**Scope:** backend/ (FastAPI), rider-app/, driver-app/, shared/, admin-dashboard/, migrations, CI/CD, deployment, tests
**Method:** Five parallel specialist audits (security, money/payments, backend perf/reliability, mobile, admin/infra/testing), findings verified against source before inclusion. Benchmark: market leaders (Uber, Lyft) adjusted for Spinr's stage.

---

## 🚨 Critical Issues & Security Flaws

### P0 — Money correctness

**1. Fare-split "card" payment never charges the card — and cancellation can mint money.**
- `backend/routes/fare_split.py:369-380` — `pay_split_share()`'s `card` branch flips the participant row to `status="paid"` with **no Stripe call anywhere in the file**. A friend "paying" their $20 share by card moves $0; the endpoint then permanently blocks retry (`Already paid` 400). The payer is never reimbursed and the debt is recorded as settled forever.
- Compounding: `fare_split.py:430-459` — `cancel_fare_split()` refunds every `status=="paid"` participant via `wallet_increment_balance` regardless of original payment method. A card participant who was never actually charged still receives a **real wallet credit** on cancel: money created from nothing. Even for genuinely charged participants, refunding a Stripe charge as a wallet credit violates the project's own "refunds always via Stripe API" rule.
- **Why it matters:** direct, currently-shippable money leak; also a fraud vector (split → "pay" by card → cancel → withdraw credit).
- **Fix:** route the card branch through `utils/stripe_charge.charge_ride` with a deterministic key (`fare-split-{participant_id}`); flip `paid` only on `succeeded`. Track `payment_method` per participant; refund card via Stripe, wallet via wallet.

**2. Admin wallet credit/debit is a lost-update race.**
- `backend/routes/admin/wallet.py:144-157, 219-242` — read balance → compute in Python → `update_one`. No row lock, no RPC — inconsistent with `repositories/wallet_repo.py`, which exists precisely to do this atomically. Concurrent rider `wallet_pay_for_ride` + admin credit = silently dropped/duplicated money, and the `wallet_transactions` ledger stops reconciling with `wallets.balance`.
- Bonus defect: `AdminDebitRequest.amount: float` (`wallet.py:55`) while the credit sibling correctly uses `Decimal` three lines up.
- **Fix:** route both endpoints through the existing atomic RPCs (`wallet_apply_credit` / negative `wallet_increment_balance`); change the float to `Decimal`.

**3. Non-idempotent DB writes silently receive read-level retries.**
- `backend/repositories/_base.py:173-196` — `run_sync()` defaults to `retry_policy="read"` (3 attempts) and only **three call sites in the codebase** override it. `insert_one`, `update_one`, and `rpc` all retry on exactly the failure class where the server may have already committed (H2 GOAWAY, disconnect, timeout-after-commit). `wallet_increment_balance` (`wallet_repo.py:31-48`) is a raw increment with no idempotency key — a lost response + retry **double-credits a wallet**. Duplicate `ride_offers`, incentive claims, and OTP rows are also possible.
- **Fix:** default `insert_one`/`rpc` to `"write"` (1 attempt); thread `retry_policy` through the CRUD signatures; grant `"idempotent_write"` only to RPCs with reference-key dedup.

**4. Corporate settlement has no idempotency guard.**
- `backend/services/payment_service.py:271-407` — `settle_corporate` has no per-ride idempotency (unlike the wallet RPC's no-op-on-already-paid and the card path's Stripe key). A crash between the allowance debit (:314) and `update_ride(..., "paid")` (:399) strands the ride in `payment_status="processing"` forever — the reclaim in `routes/rides.py:4062-4088` only reopens `processing` for the wallet method — and any manual re-drive **debits the corporate allowance/master wallet a second time**.
- Related Decimal violation on the same path: `payment_service.py:318-339` passes `_f()` floats (and a bare `floor=0.0`) across the Postgres RPC boundary — the exact Decimal→float→str round-trip the money rules forbid, on real company money, unguarded by the pre-commit hook (which only scans `fare_service.py`/`routes/payments.py`).
- **Fix:** unique constraint on `ride_payment_sources.ride_id` checked/inserted before either corporate RPC; pass Decimals straight through (`_money_str` already handles them correctly).

### P0/P1 — Security & privacy

**5. Core user-data tables have no Row-Level Security at all.**
- No migration ever enables RLS on `users`, `rides`, or `drivers`. Also uncovered: `driver_location_history` (raw GPS), `documents` (driver licenses), `notifications`, `notification_preferences`, `driver_notes`, `driver_activity_log`, `driver_daily_stats`. Tally: 101 `CREATE TABLE` vs 69 `ENABLE ROW LEVEL SECURITY`; 16 migrations create tables with no RLS in the same file — violating the repo's own RLS-first rule.
- **Why:** the only barrier between the Supabase anon key and every rider's trip history, GPS trail, and driver document is "the frontend doesn't use the anon key." One leaked key or one client-side Supabase call = P0 PII breach under the repo's own PIPEDA protocol.
- **Fix:** one retrofit migration adding RLS + explicit per-operation policies to the ~10 uncovered tables; a CI check asserting CREATE TABLE ⇒ RLS in the same file.

**6. PIPEDA logging violations on hot paths.**
- `backend/routes/maps_proxy.py:113-120` logs the raw rider search string (`input=%r`) at INFO on every autocomplete keystroke — house numbers and street addresses land in plaintext log aggregation, in direct violation of "exact addresses — log city/area only." Line 147-151 logs the top prediction's formatted address on every success.
- `driver-app/hooks/useDriverDashboard.ts:736-739` logs all four raw pickup/dropoff lat/lng values via `console.error` (feeds crash-reporting breadcrumbs).
- `driver-app/app/login.tsx:51-65` persists raw GPS to AsyncStorage **before authentication**.
- **Fix:** strip addresses/coords from log lines (length + count + flags only); geohash where a location signal is genuinely needed.

**7. Driver background GPS never stops on logout or backend-forced offline.**
- `driver-app/hooks/useDriverDashboard.ts:1348-1349` is the only call site of `stopBackgroundLocation()` — the `auto_offline` WS handler (:789-798), FCM handler (:1490-1497), and both logout paths only flip UI state. A logged-out driver's phone keeps the OS foreground service capturing and uploading location indefinitely: battery drain + PIPEDA data-minimization violation. A second leak: the foreground watcher effect (:605-711) has no cancellation guard, orphaning High-accuracy watchers on rapid state changes.
- **Fix:** stop task + geofence + clear `TRIP_ACTIVE_KEY` in the auto-offline handlers and a `registerLogoutCallback` (registry already exists in `shared/store/authStore.ts:24`); add the `cancelled`-flag pattern already used correctly in `lib/androidAuto/useCarLocation.ts:81-84`.

**8. CI security gates look blocking but cannot fail.**
- `.github/workflows/security-gates.yml` — Bandit (`|| true`, :45), ESLint-security (:82), Semgrep (:105), pip-audit (:122) all swallow failures; reports are uploaded as artifacts nobody parses. The "baselining window" header says it ended; the `|| true`s were never removed. Only Trivy, npm audit, and license checks actually block.
- `.github/workflows/ci-guardrails.yml:340,559` — the dangerous-DDL migration gate passes *literal* `$BASE_SHA` strings into `git diff` (quoted heredoc, no expansion), so `migration_files` is always empty and the DROP TABLE/TRUNCATE detector **has never run on any PR**.
- **Fix:** read SHAs via `os.environ` (adjacent steps at :393/:487 already do); remove `|| true` and add fail-on-findings steps.

**9. Admin dashboard route protection is forgeable (contained).**
- `admin-dashboard/src/middleware.ts:114-137` verifies only base64-decoded `exp` — no signature. `src/app/api/auth/set-cookie/route.ts:6-28` accepts any string from an unauthenticated caller. Anyone can mint `{"exp": 9999999999}` and render every admin page shell. Backend APIs still enforce real JWTs, so data is safe — but route-level protection is cosmetic, and the F-06 IP allowlist is bypassable at this layer.
- **Fix:** `jose.jwtVerify` in Edge middleware, or validate against the backend in set-cookie.

**10. Mobile client: unbounded 503 retry storm on money POSTs.**
- `shared/api/client.ts:727-734` — on 503 the client retries every 1.5 s forever (the catch at :729-733 is dead code; async rejections never reach it). `POST /wallet/pay` and `POST /rides/{id}/tip` carry **no Idempotency-Key** (`walletStore.ts:107-138`), so a sustained backend brownout produces an endless replay of money-moving requests from every open app — thundering herd + double-charge risk. (`createRide` is protected; the pattern exists at `rideStore.ts:672-675`.)
- **Fix:** cap at one retry; auto-retry only GETs or keyed requests; add idempotency keys to wallet/tip POSTs.

**11. Data-residency regression path.** `render.yaml:6` pins the fallback deploy to **Oregon**, and `ci.yml:380-388` auto-deploys there when the Railway CLI hiccups — silently moving Canadian PII processing to a US region, contradicting the repo's own PIPEDA residency rule. Fix: Canadian region or delete the fallback (Fly is already the standby).

---

## 🛡️ Error Handling & Telemetry

**What's done right (genuinely above average):**
- No raw errors reach users from the backend: `utils/error_handling.py:709-760` sanitizes all 5xx `detail` strings (Stripe IDs, constraint names); the general exception handler returns a generic body keyed to `X-Request-ID`; no `detail=str(e)` leaks found in routes.
- Sentry: PII scrub hooks, `send_default_pii=False`, loguru→Sentry bridge, loud production error if DSN missing (`server.py:398-480`).
- OTP lockout fails **closed** (503) on Redis outage; auth errors are logged at ERROR with context per the house rule.

**Gaps:**
1. **Mobile leaks raw transport errors to riders/drivers.** `Alert.alert('Upload Failed', err.message)` with no fallback at `driver-app/app/become-driver.tsx:270,495`, plus `ride-options.tsx:437`, `wallet.tsx:122`, `login.tsx:110`, `documents.tsx:257` — users see "Network request failed" / "JSON Parse error: Unexpected token <" or literal "undefined". The structured fields to fix this already exist (`SpinrApiError.messageKey`/`actionHint`, `client.ts:300-329`) — a central mapper is missing.
2. **A proactive token refresh strands concurrent 401s forever.** `shared/api/client.ts:163-186` — `ensureFreshToken()` never flushes `_refreshSubscribers`; requests that 401 during a proactive refresh await a promise that never settles → frozen spinners until app kill. Hits the driver "Arrived at Pickup" flow after backgrounding. The refresh-retry loop also has no cap (:695-709).
3. **Sentry scrubber is thinner than it looks.** `utils/sentry_scrub.py:31-61` redacts only message/exception strings — never `event["request"]` (URL/query string), `extra`, or breadcrumb `data`. One SDK upgrade or config drift away from shipping `/maps/reverse-geocode?lat=&lng=` query strings to Sentry.
4. **Metrics are recorded and observed by no one.** `utils/metrics.py` counters are per-process and in-memory; nothing in the repo scrapes `/metrics` (no Prometheus/Grafana/Alertmanager config). The SLA histograms the KPI table depends on die with each process. `/metrics` auth **warns** rather than fails when the token is unset in prod (`server.py:243`).
5. **Silent data loss in the insurance-critical GPS pipeline.** `utils/backgroundLocation.ts:144-156` never checks `resp.ok` (a 401 logs "Sent N points" and drops them); the REST buffer wipes after 3 failures (`useDriverDashboard.ts:528-535`). Billed distance and SGI insurance-period audit derive from these breadcrumbs.
6. **WS 30 msg/s rate limit is per-replica, not global** (`socket_manager.py:~127-160`, in-process dict) — silently weaker than the documented invariant under multi-replica fan-out.

---

## 🐢 Performance Bottlenecks & Optimizations

Measured against the repo's own SLAs (dispatch <2s, fare estimate <300ms, WS fan-out <100ms, location write <150ms):

1. **Synchronous Stripe SDK calls park the entire event loop.** `routes/payments.py:134, 501, 743-745, 758, 811`, `wallet.py:169`, `corporate_accounts.py:232`, `drivers.py:2087, 2142` — direct `stripe.X.y()` inside `async def`. Each blocks every coroutine on that worker for 100ms–1s+ (a confirming SetupIntent can exceed 1s): WS fan-out, dispatch delivery, and location writes stall **fleet-wide**. The codebase already knows the fix — `asyncio.to_thread` is used correctly at `wallet.py:196` and `payments.py:226`. This is the single highest-leverage latency fix available (one-line wraps).
2. **Dispatch matching is a capped full scan, not a geo query.** `routes/rides.py:720-724`, `dispatch_service.py:293-297` — fetch up to 500 drivers by flags, haversine-filter in Python. The PostGIS `find_nearby_drivers` RPC exists but is **dead weight**: `update_driver_location` (`driver_repo.py:119-137`) never populates the `location` geography column. Above 500 online drivers per vehicle type, page ordering is arbitrary and the nearest driver can be silently excluded — direct threat to the ≥85% match-rate KPI. Fix: populate `location` on write (or trigger), switch to the indexed RPC; interim, alert when `len(rows) == limit`.
3. **Serial WS fan-out = head-of-line blocking.** `ws_pubsub.py:309-399` + `socket_manager.py:430-448` deliver one message at a time with a 2s per-socket timeout: one broadcast hitting M half-dead sockets blocks the consumer M×2s, and every subsequent ride offer / `ride_taken` on that replica queues behind it. Fix: bounded `asyncio.gather` per broadcast.
4. **Admin N+1 at pathological scale.** `routes/admin/drivers.py:1781-1801` reads **10,000 users** then issues one ride-count per referee serially; :1609-1615 does ~400 serial round-trips (~10-20s). These occupy the shared 64-thread `_DB_EXECUTOR` that dispatch and settlement also depend on — two concurrent admin loads can starve the hot path. Fix: SQL aggregates (`GROUP BY referred_by` RPC) + `$in` batches.
5. **Rider-facing Python aggregation.** `routes/rides.py:3523` pulls up to 10,000 ride rows to `sum()` in Python per `/rides/stats` request; `surge_engine.py:80-111` counts up to 5,000 rows per area per 2-min tick (the PostGIS `SURGE_SPATIAL_COUNT` path exists but is off by default). Fix: SQL aggregates; enable the spatial count after staging rehearsal.
6. **Fare-estimate path re-reads static reference data every call.** `rides.py:1694` reloads all active service areas (plus fares, vehicle types, airport fees) per estimate — 4-6 sequential Supabase round-trips at 40-80ms each against a 300ms budget. The fix already exists in-repo (`settings_loader.py` 60s TTL cache); extend it. Same theme in dispatch: the same `service_areas` row is re-read up to ~4× per attempt, every 10s, per unmatched ride.
7. **Correctness race with perf flavor:** the legacy `_offer_timeout_handler` (`rides.py:1349-1417`, still called from `admin/rides.py:949`) resets rides to `searching` with **no status filter on the write** — a driver acceptance landing in the window is clobbered; the accepted driver is stranded. The newer batch handler does it atomically. Fix: add `{"status": "driver_assigned", "driver_id": X}` to the filter or migrate the admin path.
8. **Hygiene:** `_base.py:628-652` logs two INFO lines with full payload on every `drivers` update (go-online debug residue); `_admin_loc_last` never pruned; one process-global circuit breaker means 5 failures on any slow query 503s **all** DB traffic.

---

## 💡 Tech Stack & Architecture Recommendations (vs. Uber / Lyft)

**Where the comparison is flattering:** Uber runs ~4,500 microservices, an H3 hexagonal spatial index, a Kafka event backbone, Cadence/Temporal for payment workflow orchestration, and M3 metrics at planet scale. Lyft runs Envoy (they created it), Kubernetes, and a streaming ETA/ML stack. **Spinr should copy none of that wholesale.** A Saskatchewan-first, pre-launch platform with a single-digit-replica monolith is architecturally *correct* — Uber itself started as a monolith, and premature microservices would be the worst decision available. What's worth stealing is the *patterns*, not the topology:

| Gap | Market-leader pattern | Right-sized recommendation |
|---|---|---|
| Dispatch geo-matching does full scans | Uber H3 hex index; Lyft geo-sharded regions | You already own PostGIS — populate `location` and use the existing `find_nearby_drivers`/`match_and_claim_driver` RPCs. H3 (`h3-py`) only if/when surge zoning outgrows polygon areas. |
| Payment settlement is ad-hoc multi-step (see settle_corporate) | Uber/Lyft use durable workflow engines (Cadence/Temporal) so a crash mid-settlement resumes, never double-charges | Full Temporal is overkill today; adopt the **outbox/saga-lite pattern**: a `ride_settlements` table with a unique ride_id key + state column, driven by the existing replay-safe loop machinery. Revisit Temporal (or Temporal Cloud) when corporate volume grows. |
| DB access = supabase-py (sync) in a 64-thread pool | Purpose-built async data layers | The thread-pool + circuit-breaker infra is well-built, but PostgREST-over-HTTP adds a hop and the sync client caps concurrency at pool size. Move the 3-4 hottest paths (location write, dispatch query, fare reference reads) to **asyncpg** direct against the same Postgres — keep supabase-py for the long tail. This also unlocks LISTEN/NOTIFY as a Redis pub/sub fallback. |
| Hand-rolled in-memory metrics, no scraper | Uber M3, Lyft statsd→m3; everyone: Prometheus + OTel | Swap `utils/metrics.py` internals for **prometheus-client** (keep the metric names — they're well-designed), add **OpenTelemetry tracing** (FastAPI + httpx auto-instrumentation) for the dispatch→WS→client critical path, scrape via Grafana Cloud free tier, commit alert rules for the SLA table. Highest-leverage observability spend available. |
| No queue; 16 asyncio loops per replica ×2 platforms | Kafka everywhere | The loops are verified replay-safe — keep them. But add a lightweight **Postgres-backed job queue** (e.g. `pgqueuer`/`procrastinate`) for request-path offloading (receipts, notifications, Twilio), replacing fire-and-forget `asyncio.create_task` that dies with the process. No new infrastructure. |
| No feature flags | Uber Flipr; Lyft in-house | `app_settings` table is halfway there; formalize with **Unleash/Flagsmith** (self-hosted, free) before launch — you'll want kill-switches on surge, dispatch variants, and corporate flows. |
| No staging, load test never executed | Continuous load/chaos testing | The Locust harness (`loadtest/`) with SLA gates is *already written and good* — stand up staging (Fly.io second app, ~1 day) and schedule it. This is the cheapest de-risking available before launch. |
| Client resilience is hand-rolled per store | — | Adopt **TanStack Query** in the apps for fetch/cache/retry (fixes the 503-retry and stale-state classes structurally); delete the three dead HTTP clients first. |
| Idempotency ad-hoc per endpoint | Stripe-style idempotency keys platform-wide | Add an **idempotency-key middleware** (Redis-backed, key = header, 24h TTL) for all money-moving POSTs, and make the mobile client always send one. Converts a recurring bug class (findings 1, 3, 4, 10) into an architectural guarantee. |

**Also right as-is:** FastAPI + Pydantic, Expo + EAS, Stripe Connect, Supabase (Canadian region), Redis with documented fallback, the dual-platform standby with DNS failover (single caveat: **Redis is the real SPOF** — one `redis.spinr.ca` serves both platforms' rate-limit/OTP/WS/leader state; provision the standby Redis and alert on fallback engagement via the existing `spinr_redis_connected` metric).

---

## 🛠️ Maintainability & Code Smells

1. **Four parallel HTTP clients, three dead.** `shared/api/offlineQueue.ts` (zero callers), `shared/api/cachedClient.ts` (zero importers; would send unauthenticated requests if revived — reads a SecureStore key that's deliberately never written), `driver-app/utils/apiClient.ts` (zero importers; force-logout race if revived). Each is one accidental import from production. Delete them.
2. **Duplicate fare-estimate implementation diverging.** `features.py:928` computes `grand_total` with float `round()` — a separate code path from `routes/rides.py`'s Decimal-clean estimate; the two can quote different totals for the same trip. Same theme at `rides.py:3793-3841`: the driver-facing per-ride earnings display is raw float/round while the ledger is Decimal — can drift a cent from the ledger.
3. **Dead/contradictory config:** `ci.yml:357-378` ("Railway is the primary") and `deploy-backend.yml` both `railway up` on every main push, contradicting ADR-007/fly.toml (Fly primary). One deploy workflow should exist.
4. **Doc drift that misleads:** CLAUDE.md says next migration slot is 145 — actual highest is `205_wallet_rpc_execute_lockdown.sql` (260 files); documented runner `backend/migrate.py --env` is actually `backend/scripts/migrate.py` with no `--env`; stale comment in `routes/admin/__init__.py:32-35` misdescribes the monitoring router's gating (it's stricter than documented).
5. **Fresh-bootstrap hazard:** `scripts/migrate.py:136` sorts migrations lexicographically — `100_` applies before `10_` on any fresh environment (i.e., the staging env you need to build). The autocommit path also splits SQL on `;`, which would shred dollar-quoted function bodies.
6. **Surge override workflow is dead code in effect:** admin can store a justified >2.5× override (`admin/service_areas.py:500-537`), but `fare_service.py:383-387` unconditionally clamps to `SURGE_CAP` — the override never reaches a rider price. Arguably the right rider protection, but the admin UI/audit language implies it's live. Align one way or the other.
7. **Positive:** the conventions in CLAUDE.md are largely *real* — dual-import pattern, `_require_ride_in_state` guards, replay-safe loops, structural admin-router auth (`dependencies=[Depends(get_admin_user)]` at router level, learned from a documented prior incident), append-only migrations with rollback comments. This is a codebase where documentation and code mostly agree — rare and worth protecting.

---

## 🧪 Testing & QA (Missing Edge Cases)

**Shape:** ~288 backend test files / ~3,300 test functions with working unit/slow tiers; 10 Vitest files (~101 tests) + 3 Playwright specs (18 tests) on admin; 53 mobile test files. State machine (including the acceptance-race loser path), webhook idempotency, and corporate waterfall **are** covered — the basics are solid.

**Gaps, in order of the bugs they let through:**
1. **Fare-split payment paths have effectively no tests** — the P0 above shipped. Every `payment_method` branch of `pay_split_share` and `cancel_fare_split` needs a test asserting *money actually moved* (Stripe mock called with the right key), not just status flips.
2. **Mobile refresh-race coverage is one-sided:** `client.refresh.test.ts:293` covers 401-triggered refresh with a late subscriber, but not the *proactive*-refresh interleaving that hangs requests (finding above). Add the reverse interleaving + a subscriber-timeout assertion.
3. **The default Supabase mock silently succeeds** (`conftest.py:101-153` returns `data=[]` for anything un-overridden; typo'd query methods "work" via MagicMock auto-attributes). Add a spec'd strict variant so renamed columns/methods fail tests instead of returning empty-success.
4. **E2E is non-blocking on PRs** (`ci.yml:306-312` — `continue-on-error` on pull_request); E2E regressions merge freely and only fail on main. Coverage floor is 60% (`pytest.ini:15`) vs the documented 70-90% domain minima; ruff and the coverage-regression gate are advisory.
5. **No mobile E2E at all** (no Detox/Maestro in CI) — the driver offer→accept→navigate flow and the SOS flow are untested end-to-end on device.
6. **Load test written, never run** (`loadtest/README.md:15-17` — "Not yet executed"); `perf_baseline.py` unwired from CI. Dispatch-under-load races (offer timeout vs accept — finding M2) have no test at any tier.
7. **Missing regression tests to add with the fixes:** retry-policy double-write (H1), `settle_corporate` crash-resume, WS `ride_offer_expired` arriving after accept (driver state-wipe race, `useDriverDashboard.ts:775-808` — handlers reset state without a `ride_id` guard), WS out-of-order status regression (seq is received and discarded, `useRiderSocket.ts:282-284`).

---

## 📈 Manager's Verdict

**Overall: B+ engineering held back by C- verification loops — significantly above average for a pre-launch startup, not yet launch-ready.**

The application layer shows real engineering maturity that most companies at this stage lack: a correctly implemented JWT trust model with DB-re-derived roles, replay-safe background loops that were *verified* safe rather than assumed, disciplined error sanitization, deterministic Stripe idempotency in the retry engine, hash-locked digest-pinned supply chain, and mobile auth/refresh machinery with tests for its own races. The documented conventions are largely enforced in code. Sprint history shows a team that finds, fixes, and writes runbooks for its own incidents.

The risk is concentrated in three places. **First, money edge paths outside the main settlement flow** — fare-split, admin wallet ops, corporate settlement — were built without the idempotency/atomicity discipline the core paths have; one of them (fare-split card) is a live money leak. **Second, defense-in-depth is missing exactly where the blast radius is largest:** no RLS on `users`/`rides`/`drivers`, a forgeable admin middleware layer, a US-region fallback deploy. **Third — and most concerning as a leadership signal — several safety nets are theater:** security scanners that can't fail, a migration DDL gate that has never executed, metrics no one scrapes, a load-test harness never run, E2E that doesn't block. The team *believes* it has controls it does not have. That gap between perceived and actual coverage is how the fare-split bug shipped.

Scalability is adequate for launch (the PostGIS switch and Stripe `to_thread` wraps buy an order of magnitude); maintainability is good and improving; the monolith-plus-managed-services architecture is the right call and should be defended against premature decomposition.

**Recommended plan (three phases, ~3 sprints):**

**Phase 1 — Money & privacy blockers (this sprint, launch-gating):**
fare-split card charge + method-aware refunds; admin wallet → atomic RPCs (+ float→Decimal); `retry_policy="write"` defaults; `settle_corporate` idempotency key + Decimal pass-through; maps_proxy/driver-app PII log scrubs; mobile: flush `ensureFreshToken` subscribers, cap 503 retries, idempotency keys on wallet/tip, GPS stop on logout/auto-offline. Each with a regression test.

**Phase 2 — Make the safety nets real (next sprint):**
RLS retrofit migration + CI RLS-per-CREATE-TABLE check; fix `$BASE_SHA` env-var bug; remove `|| true` from security gates; make E2E blocking on PRs; `jose.jwtVerify` in admin middleware + authenticated set-cookie; Canadian Render region or delete fallback; numeric migration sort; delete the three dead HTTP clients; consolidate deploy workflows.

**Phase 3 — Close the performance & observability loop (sprint 3):**
populate PostGIS `location` + switch dispatch/estimate to indexed RPCs; `asyncio.to_thread` all sync Stripe calls + CI grep guard; parallel WS fan-out; TTL caches on fare reference data; prometheus-client + OTel tracing + Grafana scrape + committed alert rules; stand up staging and run the Locust suite on schedule; standby Redis provisioning; idempotency-key middleware; fix the legacy offer-timeout race.

Run this plan and the platform is in genuinely strong shape for a Saskatchewan launch — with controls that are real instead of assumed.

---

*Full per-domain findings (with file:line evidence) available from the five specialist audit passes: security, money/payments, backend performance & reliability, mobile surfaces, and admin/infra/CI/testing.*
