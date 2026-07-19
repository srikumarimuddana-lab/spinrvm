# Spinr Platform — Comprehensive Architecture & Code Review

_Date: 2026-07-19 · Scope: full read-only review of backend, rider-app, driver-app, admin-dashboard, shared, CI/CD, migrations, tests · Method: five parallel specialist audits (security, money paths, backend core/perf, mobile, admin/CI) + synthesis, benchmarked against industry leaders (Uber, Lyft)._

Repo scale at review time: ~184k LOC Python (backend), ~135k LOC TypeScript (four frontend surfaces), 347 backend test files (~3,849 tests), 300 migrations, 21 CI workflows.

---

## 🚨 Critical Issues & Security Flaws

### Money leaks (real-dollar, P0)

1. **Driver payout for uncollected fares** — `backend/routes/drivers/earnings.py:44-64`, `backend/routes/drivers/payouts.py:590-592,736-737`. `payable_balance` sums every ride with `status == 'completed'` with **no `payment_status` filter**. A ride whose rider payment failed (declined card, retries exhausted → `payment_status='failed'`) or never settled still counts toward the balance that bounds the Stripe Transfer in `request_payout`. A driver can be paid real money for a fare Spinr never collected, and `utils/stripe_reconcile.py` does not reconcile balance-vs-collected-revenue, so it goes unnoticed. **Why it matters:** direct platform cash loss that scales with payment-failure rate; on a 0% commission model there is no margin absorbing it. **Fix:** gate earnings/payable sums on `payment_status == 'paid'` (or a settled-flag column written only on successful settlement), and add a reconciliation check comparing payouts to collected revenue.

2. **Fare-split "pay by card" never charges Stripe** — `backend/routes/fare_split.py:369-380`. The card branch of `pay_split_share` marks the participant `status="paid"` (and can complete the whole split) without any `PaymentIntent` — no Stripe call exists anywhere in the file. A $20 share is recorded as paid; no card is ever charged. **Fix:** route through a real charge with a deterministic idempotency key (mirror `settle_card`/`charge_ancillary_fee`) before flipping status.

3. **Loyalty redemption race double-credits the wallet** — `backend/routes/loyalty.py:196-254`. Wallet credit is atomic (RPC) but the points debit is a non-atomic read-then-write with no conditional. Two concurrent 500-point redemptions from a 1,000-point balance both pass the check → wallet correctly gains $10, ledger records only 500 points spent. **Fix:** conditional `UPDATE ... SET points = points - :n WHERE points >= :n RETURNING` or one atomic RPC doing debit-then-credit; credit only after debit succeeds.

4. **Cancellation-fee wallet debit bypasses the atomic RPC pattern** — `backend/routes/rides/cancellation.py:133-162`. Direct read-then-write of `wallets.balance` can overwrite a concurrent top-up landing via the Stripe webhook (`wallet_apply_credit`), silently losing the top-up. Everywhere else uses `wallet_pay_for_ride`/`wallet_increment_balance`; this path should too.

### Deploy & data-exposure (P0/P1)

5. **Production deploys are not gated on tests** — `.github/workflows/deploy-backend.yml:3-11` and `deploy-fly.yml:3-11` trigger directly on push to `main` (`paths: backend/**`) with zero test/audit gating, racing in parallel with `ci.yml`'s *gated* deploy job. A red test suite still ships to both Railway and Fly. Compounding it, `ci.yml`'s topology contradicts ADR-007 (comments say Railway-primary, adds an undocumented Render fallback), the post-deploy smoke probes only the Railway/CNAME path, and Fly's health probe is skipped if the `FLY_HEALTH_URL` secret is unset (`deploy-fly.yml:101`). **Fix:** make the standalone workflows `workflow_run`-gated on CI success (or delete them and keep only the gated job), make the Fly probe mandatory, align with ADR-007, remove/confirm Render.

6. **Six user-data tables have no RLS in any migration** — `documents` (`08_complete_schema.sql:240`), `notifications` + `notification_preferences` (`48_notifications_tables.sql:10,41`), `promo_applications` (`82_...sql:53`), `staff` (`08:317`), `corporate_rides` (`08:264`). The `26_rls_coverage_gap` retrofit missed them. Anon-key exposure of driver document URLs and user notifications is a PIPEDA problem, not just a convention violation. **Fix:** one new migration enabling RLS + enumerated policies on all six.

7. **Non-atomic ride reset in the single-offer timeout handler** — `backend/routes/rides/matching.py:1027` (live via admin manual-assign, `routes/admin/rides.py:952`). It checks `status == driver_assigned`, then performs several awaits before updating with filter `{"id": ride_id}` only. A driver accept landing in that window is clobbered: the accepted ride flips back to `searching` with `driver_id=None` and re-dispatches while the driver believes they own it. Contrast `process_expired_offer` (line 1142), which claims conditionally. **Fix:** conditional UPDATE on `{id, status: driver_assigned, driver_id}`; skip side-effects on zero rows.

8. **Mobile: transient refresh failure hard-logs users out and deletes the refresh token** — `shared/api/client.ts:762-806,909-928`. If `/auth/refresh` fails transiently (network blip, backend 503 — explicitly excluded from the 503 retry at `:823`), the 401 path falls through to `setInMemoryToken(null)` + `logout()`, which deletes the SecureStore refresh token — defeating the session-preservation logic in `authStore.refreshTokens`. A few seconds of backend hiccup at token-expiry time logs out every affected user; drivers mid-shift must redo OTP. **Fix:** distinguish "refresh token rejected (401)" from "refresh transiently failed"; only clear the session on the former.

### Security hardening (P2 — posture is otherwise strong)

- `backend/routes/admin/auth.py:1385-1390` — `POST /admin/auth/unlock` is the only admin-auth endpoint with no rate limit.
- `backend/core/middleware.py:213-227` — log-context `user_id` is decoded from the JWT with `verify_signature: False`; an attacker can attribute malicious request logs to a victim's user_id, poisoning audit correlation. Set it post-auth instead.
- `backend/server.py:514` — uvicorn is not passed `ws_max_size`, so the transport accepts ~1 MiB frames; the documented 64 KB cap is enforced only after the frame is fully buffered.
- `backend/server.py:247` — `/metrics` is unauthenticated in production unless `METRICS_AUTH_TOKEN` is set (warn-only).
- Verified strong: JWT trust model (role re-read from DB for non-admin tokens, audience pinning), OTP handling (hashed, constant-time, lockout, prod-gated bypass), refresh rotation + reuse detection, Stripe webhook `claim_stripe_event` idempotency with compensating unclaim, break-glass design, CORS/security headers incl. on error responses, PII log hygiene (phone last-4, hashed emails).

---

## 🛡️ Error Handling & Telemetry

**What's done right:** the backend's 5xx sanitizer + request-ID correlation means raw exceptions don't reach mobile users; Sentry has a PII scrub (`server.py:454-466`); payment/dispatch paths genuinely never warn-and-continue; mobile uses typed errors (`SpinrApiError`, `RateLimitError`) and `getApiErrorMessage` for user-facing copy; SOS is exempt from all session-clearing logic; error reporting is Sentry-primary/Crashlytics-fallback with PIPEDA scrubbing.

**Gaps — user-facing leakage:**
- `shared/components/ErrorBoundary.tsx:36-56` renders `error.name: error.message` **plus the full JS/component stack** to riders and drivers in production (deliberate, "diagnosable from a screenshot"). Sentry already captures the stack; gate diagnostics behind `__DEV__` or a tap-to-reveal error-code + request-ID affordance.
- `rider-app/app/loyalty.tsx:140` shows raw `err.message` ("Request failed with status code 500") — one stray from the `getApiErrorMessage` convention.
- Admin toasts surface backend `detail` strings verbatim (`promotions/page.tsx:451` et al.) — internal-message leakage to authenticated admins only; acceptable but worth centralizing.
- `backend/routes/admin/driver_import.py:67,90` pass raw `str(e)` as 4xx detail (4xx bypasses the sanitizer).
- Silent `catch {}` in five admin pages (`earnings/page.tsx:1281`, `safety/page.tsx:194`, `driver-notes.tsx:81`, `tickets.tsx:231`, `service-areas/page.tsx:87,119,1530`) — ops act on stale data with no toast, no log. Violates the project's own no-silent-swallow rule.

**Gaps — admin/ops observability:**
- **Loop watchdog covers 17 of ~26 background loops** (`core/lifespan.py:434-454`). Missing: `safety_checkin` (a safety loop), `preauth_capture`, `reconciliation`, `referral_payout`, `driver_claim_reaper`, `route_finalizer`, `route_gap_monitor`, `suspension_reactivation`, `zoho_desk_sync`. These can die silently. (CLAUDE.md's "16 loops" is stale.)
- **Metric coverage vs the SLA table is partial.** Present: fare calc, settlement, offer sent/accepted, offer-to-accept histogram, WS fan-out publish. Missing: driver-location-write duration (150 ms SLA), auth-token-refresh duration (200 ms SLA), Stripe-webhook-processing duration (500 ms SLA). The WS fan-out metric measures publish-to-Redis only — the serial-consumer stall (below) is invisible to it.
- `ci.yml:677` — the Slack failure notifier doesn't watch `e2e-test`, `security-scan`, `docker-image-scan`, or `smoke-test`; failures there never alert anyone.

---

## 🐢 Performance Bottlenecks & Optimizations

Ordered by which breaks first under load:

1. **Serial WebSocket unicast delivery** — `backend/utils/ws_pubsub.py:324-414`. The pub/sub consumer delivers unicasts one at a time, each with a 2 s `wait_for` (`socket_manager.py:313`). One half-open mobile socket queues *every* offer/`ride_taken`/status event on that replica behind a 2 s stall; a handful of bad connections serializes fan-out to multi-second — breaching both the <100 ms fan-out and <2 s dispatch SLAs. Broadcasts already use `asyncio.gather`; unicasts need concurrent dispatch with per-socket ordering (bounded per-client queues).

2. **Dispatch attempt = 15–25 serial Supabase round-trips** — `backend/routes/rides/matching.py:151-930`. `service_areas` read up to 4× per attempt, `driver_subscriptions` twice, serial per-driver claim + full re-read, N+1 `quest_progress` per claimed driver, plus a 1.2 s Maps ETA budget — little headroom under the <2 s P95, and every timeout re-dispatch repeats it all. Fix: fetch the area once and thread it through; merge subscription queries; batch post-claim re-reads with `.in_()`; batch the quest hint.

3. **DB access layer: 64-thread pool over effectively one HTTP/2 connection** — `backend/repositories/_base.py:215-228`. All queries are sync `supabase-py` calls through `run_sync`; on deadline expiry `future.cancel()` cannot stop a running thread, so slow-DB storms fill the pool with zombie work and healthy requests queue behind it. The GOAWAY retry machinery is papering over a structural single-connection bottleneck. This is the platform's 10x throughput ceiling.

4. **Location hot path** — `backend/routes/websocket.py:915-917`: up to 499 serial `redis_incr` calls per location batch (`redis_incrby` exists, unused) against a 150 ms SLA. `:983-997`: the batch path bypasses the 5 s active-rides cache the single-ping path uses. `:1099-1152`: `get_nearby_drivers` fetches an arbitrary global 100 online drivers with `SELECT *` and haversine-filters in Python — wrong/empty results once >100 drivers are online province-wide.

5. **Surge engine N+1 with full-fleet Python scans** — `backend/utils/surge_engine.py:82-196`. Per-area rides query + up-to-5000-row driver fetch + Python point-in-polygon per 2-minute tick; `get_surge_status` repeats it on every admin dashboard load. **The PostGIS fix already exists** (migration 170, `_count_supply_spatial`) but is off by default (`SURGE_SPATIAL_COUNT`).

6. **Scheduled dispatch is serial** — `backend/utils/scheduled_rides.py:285-303`. A morning burst of N due rides dispatches ride N minutes late; the reminder path pushes then flags non-atomically and the leader lock fails open on Redis error (duplicate reminders multi-replica).

7. **Seven stacked `BaseHTTPMiddleware` layers** (+ an unverified `jwt.decode` per request) add fixed per-request cost on every SLA path; consolidate into 1–2 pure-ASGI middlewares.

8. **Admin dashboard**: payouts page fetches the full unbounded list then paginates client-side (`payouts/page.tsx:163`); KYB queue hard-caps at 100 with silent truncation (`kyb-queue/page.tsx:61`).

9. Hygiene: `spinr:ws:seq:{client_id}` Redis keys never expire (one immortal key per client ever seen); unbatched full-table `UPDATE` backfills in migrations 101/104/118/168 vs the <30 s migration SLA.

---

## 💡 Tech Stack & Architecture Recommendations (vs Uber / Lyft)

**Where the stack is right for the stage.** A single FastAPI monolith with Supabase + Redis is the correct choice at Saskatchewan-launch scale — Uber ran a monolith for years before its SOA/DOMA migration, and premature microservices would be a mistake here. State is correctly externalized, so horizontal scale-out is real. Do not decompose; fix the layers below inside the monolith.

**1. Geospatial indexing — the biggest conceptual gap vs leaders.** Uber built H3 (hexagonal hierarchical indexing) and Lyft geosharded Redis precisely because Python haversine over row fetches stops working at density. Spinr already built the answer — PostGIS (migration 170), a `match_and_claim_driver` RPC with `SKIP LOCKED`, and `SURGE_SPATIAL_COUNT` — **and none of it is enabled on the live path**. The cheapest 10x-readiness win in the codebase is flipping these on and deleting the Python point-in-polygon/haversine paths. H3 (via the `h3-py` lib) is worth adopting later for demand forecasting/heatmap cells; PostGIS suffices for matching.

**2. Async DB access.** Migrate hot paths (dispatch, location, fare) off sync `supabase-py`-in-threadpool to an async PostgREST client or `asyncpg` + PgBouncer. This removes the thread tax, the zombie-thread deadline problem, and the GOAWAY class of errors in one move. Keep the repository layer; swap its engine.

**3. Workflow orchestration.** Uber built Cadence (now Temporal) because hand-rolled retry loops don't scale organizationally. Spinr has ~26 background loops, each with bespoke replay-safety. The three money blockers above are all "multi-step money movement without a saga" bugs. Adopting Temporal today is heavy for this stage; the right intermediate step is (a) an **outbox pattern** for money movements (the `financial_events` ledger is already halfway there — make it the source of truth that a single settler consumes), and (b) evaluate Temporal when loop count passes ~35 or a second money-saga bug ships.

**4. Observability.** Uber built Jaeger; Lyft built Envoy largely for observability + resilience. Spinr has request-ID propagation and good metrics naming but partial SLA coverage, no tracing, and **no external synthetic monitoring** (a total outage is discovered by users — tracked as E4, still open). Priority order: synthetic probes + SLO alerts (Checkly/Grafana, days of work), fill the three missing SLA metrics, then OpenTelemetry tracing only when multi-replica latency debugging hurts (D2, correctly deferred).

**5. Release safety.** Every leader ships behind feature flags with kill switches (Uber's Flipr). E5 (kill switches for surge/dispatch/promo/corporate) is still open and is cheap insurance — a misbehaving subsystem currently requires a deploy to disable. Combined with the ungated deploy finding (H1) and no staging (E1), release safety is the weakest pillar vs industry practice: the fix sequence is gate deploys → staging env → run the already-built Locust marketplace sim → kill switches → forced-upgrade gate for mobile (E3, impossible to retrofit later).

**6. Product-level parity gaps** (already tracked, validated as real): driver destination mode (D3 — the single biggest driver-retention feature vs Uber/Lyft), demand heatmap UI (D4 — server side exists), upfront-pricing ML and in-house ETA are *not* needed at this scale (Google Maps + the existing movement-gate cost controls are the right trade).

---

## 🛠️ Maintainability & Code Smells

- **God files:** `routes/admin/rides.py` (3,363 lines), `routes/admin/drivers.py` (2,655), `features.py` (1,910), `routes/drivers/subscriptions.py` (1,883), `webhooks.py` (1,828). The route files mix routing, authz, business logic, and DB access; extraction into the existing `services/` layer should be opportunistic (when touched), not big-bang.
- **Documentation drift is systemic:** CLAUDE.md says 16 background loops (real: ~26), highest migration 144 (real: past 229), and mandates reading `graphify-out/GRAPH_REPORT.md` which **does not exist in the repo**. Drifted docs that agents and humans treat as authoritative are worse than no docs.
- **Migration prefix hygiene has collapsed:** duplicate numeric prefixes far beyond the documented list (80–84, 100×3, 110×3, 114×3, 224×3, …). The CI prefix-uniqueness gate is ineffective or baseline-frozen — audit it, or the "no new duplicates" rule is fiction.
- **Dead code with latent bugs:** `shared/api/cachedClient.ts` (reads a token key the auth store deletes — any future consumer silently unauthenticated); `cors_exception_handler` (registered then superseded); the CI Postgres service container no test touches; 5 test files asserting the Mongo-era `to_list()` API.
- **Decimal-discipline smells** (safe today, drift-prone): float constants in `fare_service.py:31-37` (the pre-commit-guarded file), float math throughout `surge_engine.py`, display-only `_f()` used for internal RPC params in `payment_service.settle_corporate`.
- **Convention strays:** `useRiderSocket` claims connected before auth completes and lacks the driver hook's heartbeat watchdog; driver reconnect circuit breaker has no NetInfo-restore reset; offer `fare` typed number-from-WS but string-from-FCM; web token-clearing localStorage/sessionStorage mismatch.
- **Receipt inconsistency:** the emailed/PDF tax receipt (7-year retention) shows surge as a text notice with no dollar amount while the in-app breakdown computes a real `surge_delta` (`utils/email_receipt.py:135-142`) — a transparency-policy violation on the *retained* document. Reuse `build_fare_breakdown_lines`.

---

## 🧪 Testing & QA (Missing Edge Cases)

- **~3,849 backend tests, 100% mocked.** The `mock_supabase_client` fixture is an accept-anything chainable MagicMock with an *async* `execute` while production is sync-through-threadpool — query-builder typos pass tests. No integration tier runs in CI (the provisioned Postgres container is dead weight), therefore **zero executable RLS tests** — which is exactly how six tables shipped without RLS.
- **Stripe webhook coverage: 11 of ~20 handled event types tested.** Untested include `charge.refunded`, `charge.dispute.created/closed`, `charge.captured` — the refund/dispute money paths. Violates the project's own "every webhook type tested before production" rule.
- **Coverage enforcement is theater in three places:** backend floor is 60% vs the mandated 90% on payments/fare (A1, open); admin vitest thresholds exist but CI never runs `--coverage`; the coverage-regression gate, ruff lint, lint-trend, and breaking-change gates are all `continue-on-error: true`.
- **E2E:** 12 Maestro mobile flows exist but no workflow runs them; Playwright admin E2E is non-blocking on PRs and `deploy-admin` doesn't depend on it; the Locust marketplace load sim is complete but has never executed (blocked on staging).
- **Missing edge-case tests exposed by this review:** concurrent loyalty redemption; payment-failed ride → payout eligibility; fare-split card path actually charging; offer-timeout vs driver-accept race; WS transport frame >64 KB; refresh-transient-failure session preservation on mobile; RLS allowed/denied pairs per table.

---

## 📈 Manager's Verdict

**Overall grade: B+ engineering, C− release discipline.** This is an unusually mature codebase for a pre-launch startup: the hard distributed-systems edges (token rotation races, accept-race 409s, idempotent retries, replay-safe loops, half-open sockets) are not just handled but documented in-line with the incidents that motivated them, and the security/money core (JWT trust model, OTP, Stripe idempotency, Decimal discipline, corporate wallet RPCs, PII hygiene) verified clean under adversarial review. The team's instinct to encode conventions in CLAUDE.md and enforce them in CI is the right one.

The risk is concentrated in two places. First, **three real-dollar money leaks** (uncollected-fare payouts, the no-charge fare-split card path, the loyalty race) share one root cause: multi-step money movements built as independent writes instead of atomic claims/sagas — the same discipline the wallet RPCs already model. Second, **the guardrails don't bite**: deploys bypass the test gate, half the CI quality gates are advisory, coverage mandates are unenforced, the migration-prefix check is dead, and docs have drifted from reality. A team this good at writing checks needs to be equally ruthless about making them blocking — an advisory gate is a decision to ship the failure.

Scaling posture: the monolith is the right architecture; what breaks first, in order, is WS fan-out under bad sockets, dispatch round-trip latency, then DB thread-pool saturation — and notably, the fixes for two of the three (PostGIS matching, spatial surge) are **already built and merely disabled**.

### The Plan

**Phase 0 — stop the bleeding (this week, launch-blocking):**
1. Gate `payable_balance`/payouts on `payment_status='paid'` + payout-vs-collected reconciliation check (earnings.py, payouts.py).
2. Wire fare-split card branch through Stripe with idempotency key (fare_split.py:369).
3. Atomic loyalty debit (conditional UPDATE or single RPC) (loyalty.py:196).
4. Gate/delete the standalone deploy workflows; mandatory Fly health probe; align ci.yml with ADR-007.
5. RLS migration for the six uncovered tables.
6. Conditional UPDATE in `_offer_timeout_handler` (matching.py:1027).
7. Mobile: preserve session on transient refresh failure; add retry-depth guard (shared/api/client.ts).

**Phase 1 — pre-launch hardening (2 weeks):**
8. Cancellation-fee debit → atomic wallet RPC; surge dollar line on email/PDF receipts.
9. Webhook tests for all handled Stripe types (refunds/disputes first); regression tests for every Phase-0 fix.
10. Real-DB integration tier in CI (apply migrations, RLS allowed/denied probes) — resolves the dead Postgres container.
11. Coverage ratchet toward the 90% money-path mandate (A1); flip ruff, coverage-regression, and admin coverage to blocking.
12. Watchdog coverage for all ~26 loops; three missing SLA metrics (location write, token refresh, webhook duration); `ws_max_size` on uvicorn; rate-limit `/admin/auth/unlock`; post-auth log-context user_id.
13. ErrorBoundary: stop rendering stacks in production; fix loyalty.tsx raw error; sweep admin silent catches.
14. External synthetic monitoring + SLO alerts (E4) — days of work, closes the "users discover outages" hole.

**Phase 2 — scale readiness (4–6 weeks):**
15. Concurrent WS unicast delivery with per-socket ordering; sharded pub/sub channels.
16. Dispatch round-trip reduction (single area fetch, merged subscription queries, batched claims/re-reads/quests); enable `SURGE_SPATIAL_COUNT` and the `match_and_claim_driver` PostGIS path; fix `get_nearby_drivers` geo pre-filter; `redis_incrby` on the batch limiter.
17. Async DB client (async PostgREST or asyncpg+PgBouncer) on dispatch/location/fare hot paths.
18. Staging environment (E1) → execute the Locust marketplace sim (E2) → record breaking points.
19. Kill switches for surge/dispatch/promo/corporate (E5); forced-upgrade gate for mobile (E3).
20. Concurrent scheduled-dispatch tick with atomic reminder claims; middleware consolidation to pure ASGI.

**Phase 3 — parity & platform (post-launch):**
21. Driver destination mode (D3) and heatmap UI (D4) — the two biggest driver-retention gaps vs Uber/Lyft.
22. Outbox pattern over `financial_events` for money movements; evaluate Temporal when the loop count or saga-bug count says so.
23. OpenTelemetry tracing (D2); H3 cells for demand forecasting; admin payouts/KYB server-side pagination; migration-prefix CI check revival; CLAUDE.md drift sweep (loop count, migration high-water mark, graphify references).

**Hygiene rule going forward:** no new advisory CI gates — a check either blocks or it doesn't exist; and every multi-step money movement goes through an atomic RPC or the outbox, never independent writes.
