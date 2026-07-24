# Spinr Platform — Comprehensive Architecture & Code-Quality Teardown

**Date:** 2026-07-24 · **Type:** read-only review (no code changed) · **Scope:** all five surfaces (backend, rider-app, driver-app, admin-dashboard, shared) + CI/deploy, benchmarked against Uber/Lyft-class architecture.

**Method:** five parallel specialist review passes (security, money paths, backend architecture/performance, client surfaces, testing/QA), cross-checked against `.claude/context/sprint-current.md` and `ACTION_ITEMS.md` so already-shipped fixes are not re-reported. Every finding below is verified against current code with file:line references.

**Headline:** the platform is materially more mature than a typical pre-launch ride-share codebase — but this review found **one true P0** (an unauthenticated endpoint serving driver government-ID documents), a cluster of P1s in the shared token-refresh machinery and refund/receipt edges, and a set of well-understood ~10× scaling ceilings. A prioritized plan is at the end.

---

## 🚨 Critical Issues & Security Flaws

### P0 — Unauthenticated access to driver government-ID documents
- **Where:** `backend/documents.py:875,977-1000`, mounted publicly at `/api/documents/{file_id}` and `/api/v1/documents/{file_id}` (`backend/server.py:403-404`).
- **What:** `files_router` has **no auth dependency**, and its handler takes zero auth. It either streams a legacy `document_files` blob or 302-redirects to the Supabase Storage signed URL of a `driver_documents` row — driver's licenses, vehicle registrations, IDs — with no ownership or admin check. Document IDs are UUIDs but are referenced in other API responses, so they are obtainable.
- **Why it matters:** anyone holding a document ID can pull another person's government-ID-class document. This is a PIPEDA breach-protocol event waiting to happen (P0 incident class per `CLAUDE.md`).
- **Fix:** add `Depends(get_current_user)` + ownership check (`document.driver_id → user_id == current_user["id"]`) or `Depends(get_admin_user)`; retire the apparently-dead unauthenticated `document_files` fallback. Trivial — the exact pattern exists everywhere else in the codebase.

### P1 — Unlinked Stripe refunds vanish from the books
- **Where:** `backend/routes/webhooks.py:897-906` (`charge.refunded` handler).
- **What:** when a refund event's `payment_intent` matches no ride, the handler `logger.warning`s and falls through; the event is still marked `processed`, so reconciliation can never replay it. The `payment_intent.succeeded` branch just above treats the same situation as CRITICAL and forces a Stripe retry — the refund branch has no equivalent.
- **Why it matters:** real money leaves the Stripe account with zero trace in Spinr's ledger; violates the repo's own "never `logger.warning` and continue on a payment error" rule; permanent reconciliation blind spot.
- **Fix:** mirror the succeeded-branch pattern — `logger.error` + orphan-refund ledger row + admin broadcast; only mark processed after the write succeeds.

### P1 — Token-refresh concurrency flaws in the shared API client (both mobile apps)
1. **Unbounded 401→refresh→retry loop** — `shared/api/client.ts:774-824` with `retryFn` recursion at `:982/:1011`. No retry counter: a fresh request carries a fresh `retryFn`, so an endpoint that persistently 401s for a non-expiry reason (suspended user, role mismatch, clock skew) while `/auth/refresh` succeeds loops forever — hung spinners, backend hammering, battery drain. **Fix:** thread an attempt count / `_retry` flag; cap at one refresh-retry per logical request.
2. **Proactive-refresh subscriber deadlock** — `shared/api/client.ts:164-187` vs `:782-797`. `ensureFreshToken()` never calls `_onRefreshed()`; a request that 401s while a proactive refresh is in flight subscribes to a promise nobody settles and hangs indefinitely — exactly on app foregrounding, when `ensureFreshToken` runs. **Fix:** flush subscribers in `ensureFreshToken`'s `finally`, or unify both paths behind one refresh coordinator.

### P1 — GPS-blind driver stays dispatchable
- **Where:** `driver-app/hooks/useDriverDashboard.ts:645` (watcher has no error handler), `:439-448`, `:1396-1402`; `backgroundLocation.ts:238-239,320-324` silently no-op on revocation.
- **What:** if a driver revokes location permission mid-shift, `watchPositionAsync` silently dies; nothing detects it until the next foreground event, and even then the app only blocks the UI — `is_online` stays true server-side, so dispatch can offer rides to a driver with no GPS. Also intersects the insurance-period model (Period 1 assumes a functioning location contract).
- **Fix:** watchdog on watcher errors/staleness that force-toggles the driver offline server-side with an explanatory prompt.

### P1 — Email-OTP path has no brute-force lockout
- **Where:** `backend/routes/auth.py:638-663` (`verify_company_email_otp`).
- **What:** the phone flow's `_check_otp_lockout`/`_record_otp_failure` (5 failures/hr → 24 h lockout) was never wired into the company-email OTP verify path; only a generic `5/minute` limiter applies — ~300 guesses/hour against a target email, ~60× the stated OTP guess budget, with no lockout signal to alert on.
- **Fix:** reuse the same lockout helpers keyed by email.

### P2 — Security/privacy warnings (fix before next PIPEDA-relevant merge)
- **Disputes re-leak full legal names** — `backend/routes/disputes.py:160-186` re-derives `first_name + last_name` per row in the admin bulk list, silently reintroducing the exact leak migration 142 scrubbed at rest. (ACTION_ITEMS B2, still open for this sub-item.)
- **VIN encryption rollback without visible privacy sign-off** — `backend/migrations/244_vehicle_vin_plaintext_at_rest.sql` deliberately decrypts vault-encrypted VINs back to plaintext for admin convenience; the migration's own comment admits the exposure. Needs documented legal/privacy review or reversal.
- **Reviewer bypass grants free production rides** — `backend/routes/auth.py:328-347` + `backend/routes/payments.py:329-346`: the App-Store-reviewer allow-list also unlocks `pi_mock_*` payment confirmation in production. One leaked env value = account takeover **and** unpaid rides. Gate the mock-payment path on a narrower, capped flag.
- **WS rate limit is per-replica** — `backend/socket_manager.py:27-43`: a user balancing sockets across replicas gets `replicas × 30` msg/s (ACTION_ITEMS B4, open). Promote to Redis `INCR`+`EXPIRE`.
- **`/metrics` readable without auth until token set** — `server.py:246-249` only warns when `METRICS_AUTH_TOKEN` is unset in production.
- **Admin `set-cookie` route accepts arbitrary token bodies** — `admin-dashboard/src/app/api/auth/set-cookie/route.ts:6-28`, no structure/exp validation (defense-in-depth gap; backend still verifies signatures).

**Posture note:** the security pass verified admin routers are centrally auth-gated by default, Stripe webhook signature+idempotency handling is correct, and previously-reported items (HttpOnly admin cookie, MFA, vault fail-closed, CORS fail-fast, disputes RLS via migration 142) are genuinely closed. The P0 is an outlier, not a pattern.

---

## 🛡️ Error Handling & Telemetry

**What's genuinely good (keep it):** central 5xx sanitization in `backend/utils/error_handling.py:44-60` scrubs stray `detail=str(e)` before clients see it; generic handler returns `request_id`-bearing bodies with CORS preserved; `DatabaseError` carries `details["original"]`; the circuit breaker correctly excludes application-level errors (`repositories/_base.py:300-309`); loguru→Sentry bridge works; **every** KPI metric name documented in CLAUDE.md is actually emitted by `utils/metrics.py`. On mobile, `getApiErrorMessage()` (`shared/api/client.ts:417-470`) strips axios/Hermes noise and is **enforced by ESLint rule** with regression tests — no raw error reaches a rider/driver anywhere. That is rare discipline.

**Gaps:**

1. **Raw GPS + addresses in logs (PIPEDA violation, backend):**
   - `backend/routes/rides/estimates.py:166-171,194-199,216-221` — rider lat/lng at INFO with `%.5f` precision (dispatch path removed this; estimates didn't).
   - `backend/routes/maps_proxy.py:114-120,147-151` — raw `"lat,lng"`, the rider's free-text address search string, and result addresses logged; not covered by `sentry_scrub.py` (which scrubs message strings, not structured args).
   - `backend/routes/rides/matching.py:44-47` — deprecation warning formats raw pickup coords.
2. **Sentry domain tags missing exactly where they matter** — the bridge + `sentry_scrub.py` tag-promotion exist and 78 call sites use them, but **zero** usage in `routes/rides/matching.py`, `routes/rides/booking.py`, `routes/rides/payments.py`, `routes/payments.py`, `services/dispatch_service.py`, `services/fare_service.py`. Dispatch/payment/fare errors arrive untagged; triage-by-domain fails on the highest-value domains.
3. **Admin dashboard swallows outages into blank tables** — ~40 sites of `.catch(() => setX([]))` (`drivers/page.tsx:255`, `earnings/page.tsx:1045,1066`, disputes, subscriptions, safety, promotions). An operator cannot tell "no drivers online" from "backend down." Violates the repo's own no-silent-swallow rule. Web surfaces also show raw `e.message` in UI (`login/page.tsx:79,112,158`, `register/driver/page.tsx:92,123,204`, `mfa-enroll-dialog.tsx:53,68`, `settings/page.tsx:106`, `monitoring-map.tsx:419`, `rides/live/[id]/page.tsx:37`) — mobile got the sanitizer + lint; web never did.
4. **WS delivery failures are metric-less** — `backend/socket_manager.py:324-334` logs send failures only to `diag_logger.info`; a systematic delivery failure would be dashboard-invisible despite the <100 ms fan-out SLA. Publish failures in `ws_pubsub` are logged but also uncounted.
5. **Surge acts on failed reads** — `surge_engine.py:111-113,197-202`: demand/supply count failures return 0 → multiplier written as 1.0×. Logged at ERROR (not silent), but the tick should skip the area rather than act on a fabricated value for a regulated price.
6. **4xx `detail=str(e)` pass-through** — `routes/admin/driver_import.py:67,90` return raw parse-exception text (4xx bypasses the sanitizer by design). Admin-only, low risk.

---

## 🐢 Performance Bottlenecks & Optimizations

Ordered by when they bite. Nothing here is broken *today* at Saskatchewan scale; these are the ceilings.

1. **The DB path is the wall (~10×): sync supabase-py over PostgREST in a 64-thread pool** — `backend/repositories/_base.py:138-139,181-324`. Every DB op is an HTTP round-trip on a dedicated thread; hard cap of 64 concurrent DB ops/replica (~1–4 k ops/s), and dispatch/booking burn 10–25 sequential ops each. A slow admin export competes with dispatch for the same threads (no bulkheads). The queue-depth gauge (`spinr_db_thread_pool_queue_depth`) will show saturation first — watch it. **Fix path:** asyncpg/direct Postgres with a connection pool for the ~5 hot paths (dispatch candidate query, ride claim, location writes, fare settle); keep PostgREST for long-tail CRUD.
2. **Timed-out DB calls don't free their threads** — `_base.py:215-228`: `future.cancel()` can't cancel a running thread; under sustained overload, 503s are returned while threads stay busy — capacity collapses instead of shedding. **Fix:** per-call httpx timeouts on the supabase client below the request deadline.
3. **Dispatch matching is a btree box scan + Python haversine, capped at 500 rows** — `backend/routes/rides/matching.py:255-292`, `services/dispatch_service.py:75-102`. A trigger-maintained PostGIS `location_geog` + GiST index **already exists** (migration 170) but dispatch doesn't use it, and `driver_repo.py:107-116`'s `find_nearby_drivers` RPC reads an unpopulated column (dead code / foot-gun). **Fix:** one `ST_DWithin` RPC over the existing index; delete the dead RPC.
4. **One dispatch attempt = 10–25 sequential round-trips, incl. an N+1 per-driver quest lookup** — `matching.py:151-930` (quest N+1 at `:795-822`, sequential 2-RTT claims at `:681-696`) — the <2 s offer SLA stacks directly on ceiling #1. **Fix:** batch quest lookup via `.in_()`, fold subscription/plan/quota into one RPC, bulk conditional claim.
5. **Single global Redis pub/sub channel; serial unicast delivery** — `utils/ws_pubsub.py:49,324-414`. Every replica decodes the full firehose; the consumer delivers unicasts sequentially, so two stuck sockets add up to 4 s head-of-line delay against a <100 ms SLA. **Fix:** deliver concurrently (the broadcast path already does — `socket_manager.py:448-476`); shard the channel when replicas >2–3.
6. **Surge engine ships up to 5,000 full rows over REST every 2 min, counts in Python** — `surge_engine.py:46,82-113,161-202`. The PostGIS `drivers_available_in_polygon` RPC exists behind `SURGE_SPATIAL_COUNT` but is **off by default** (ACTION_ITEMS D1). **Fix:** rehearse and flip the flag; make demand a `count="exact"` query.
7. **Fare-estimate re-fetches all service areas per call + sequential per-stop geofencing** — `routes/rides/estimates.py:152,189-231,272-292`, against a <300 ms P95. **Fix:** 30–60 s cache of active service areas; batch geofence checks.
8. **Non-atomic acceptance-rate EWMA** — `driver_repo.py:274-295`: read-modify-write across two round-trips; concurrent expiries can lose updates that feed ETA ranking. **Fix:** single SQL `UPDATE ... SET acceptance_rate = 0.1*:o + 0.9*acceptance_rate`.
9. **Admin surface hot spots** — promotions user-picker downloads the **entire users table per debounced keystroke** (`promotions/page.tsx:286,304` → `api.ts:1938-1939`; a proper `adminSearchUsers` already exists); earnings dashboard computes finance totals over a silent 500-row cap (`earnings/page.tsx:1060,1082,1093`) — **wrong money totals**, not just slow. **Fix:** switch picker to server search; server-side aggregates + pagination for earnings.
10. **T4A annual job is a classic N+1 over up to 10,000 drivers** — `utils/t4a_annual_job.py:105-118`; tolerable yearly, but CRA-deadline risk grows with fleet size. **Fix:** one `.in_()` batch fetch + aggregate.

Verified state of documented open items: D2 (no OTel — only X-Request-ID) open; D7 substantially superseded by the `admin_cancellation_breakdown` RPC; D8 partially done (`broadcast_to_admins` added, per-admin loop retained as backstop); B4 open as documented.

---

## 💡 Tech Stack & Architecture Recommendations (vs Uber/Lyft)

### Where the market leaders differ — and what's actually worth copying

| Concern | Uber / Lyft | Spinr today | Assessment |
|---|---|---|---|
| Geospatial matching | Uber H3 hex grid; Lyft S2/geohash sharding — nearest-driver O(ring cells) at city scale | btree box scan + Python haversine, 500-row cap; **unused** PostGIS GiST index already built (migration 170) | Fine at launch scale. The gap is *activation, not construction* — switch dispatch + surge to the existing PostGIS index; H3 only ever matters multi-city |
| Dispatch engine | Uber DISCO — dedicated stateful matching service, batched offer windows | Inline request-scoped asyncio timers + atomic DB claims + 10 s/60 s sweeper backstops (genuinely replay-safe) | Sound design; post-deploy offer latency floors to the reaper interval. Eventual step: DB/Redis-backed offer queue + claim-based worker — the reaper is already 80 % of it |
| Event backbone | Kafka — every ride event durable, feeds pricing/fraud/ML/analytics | Single fire-and-forget Redis pub/sub channel; no replay | **Biggest architectural delta.** Right-sized step is Redis Streams (consumer groups + replay), not Kafka |
| DB access | Sharded stores + pooled native connections | Supabase REST via sync client in 64 threads | Correct but ceiling-limited; asyncpg for hot paths is the highest-leverage single change on this list |
| Realtime | gRPC/QUIC streams, edge POPs | One WS per client + Redis fan-out | Adequate; fix serial unicast delivery and channel sharding first |
| Pricing/ML | Michelangelo, real-time demand pricing | Rule-based tiers, 2.5× hard cap; `demand_forecast.py` exists, unused in driver UI | Rule-based is **correct** for Spinr's regulatory posture — the cap is a feature. Surface the heatmap (D4) rather than adding ML |
| Mobile | Native Swift/Kotlin (RIBs), server-driven UI | Expo RN SDK 54 + zustand + shared TS package | Right choice at this team size; EAS + OTA ≈ small-team parity |
| Observability | Jaeger, M3, full tracing | Sentry + loguru bridge, Prometheus metrics, X-Request-ID | Reasonable; OTel deferral (D2) is defensible pre-launch |

### Right-sized additions (in leverage order)
1. **Activate the PostGIS you already built** — dispatch `ST_DWithin` RPC + flip `SURGE_SPATIAL_COUNT` (closes D1 and perf items #3/#6 with near-zero new construction).
2. **asyncpg (or Supavisor-pooled direct Postgres) for the ~5 hot paths** — raises the primary throughput ceiling without touching the long tail.
3. **Redis Streams for ride events** — durable replay after WS reconnect, audit trail, prereq for any future fraud/analytics pipeline.
4. **Kill switches (E5)** — `app_settings` booleans checked at loop/path tops for surge, scheduled dispatch, promos, corporate billing; incumbents can disable a misbehaving subsystem in seconds, Spinr needs a deploy.
5. **Staging + synthetic monitoring (E1/E4)** — the single largest operational-maturity gap vs any incumbent; load-test execution (E2), DAST (E6), and migration rehearsal are all queued behind it.
6. **Forced-upgrade gate (E3)** — min-supported-version check; impossible to retrofit once old binaries are in the wild.
7. **Client-side money-POST idempotency** — attach `Idempotency-Key` on all money POSTs (wallet pay, tip) so the 503 auto-replay (`client.ts:841-856`) is safe by construction, not by backend guard alone.

---

## 🛠️ Maintainability & Code Smells

1. **Dead/zombie code that can hurt someone:**
   - `shared/api/cachedClient.ts` — zero call sites, exported from the package, bypasses auth/timeout/CSRF entirely and reads a token store the auth flow deliberately deletes (`authStore.ts:246-247`). Delete before someone imports it.
   - `frontend/` tree — stale fork of the stores that renders raw `error.message` (`frontend/store/rideStore.ts:172-322`, 10 sites). Archive it.
   - Rider offline queue is **drain-only** — `rideStore.ts:544-626` replays `create_ride` (no idempotency key, no TTL, 3 blind retries) from a queue **nothing ever writes to** (verified repo-wide). Dead today; a double-booking bug the day someone wires the enqueue side. Delete or finish properly.
   - `find_nearby_drivers` RPC reads an unpopulated column (`driver_repo.py:107-116`).
2. **Web auth-token storage inconsistency** — token read from `sessionStorage` (`client.ts:252`) but cleared from `localStorage` (`:937-943`); `cachedClient` reads a third location. Pick one per platform.
3. **Tracker drift** — `ACTION_ITEMS.md` B2's rounding sub-item is actually fixed (`disputes.py:227` now uses `dollars_to_cents`, HALF_UP); D7 substantially superseded; sprint notes credit PR #266 with "Playwright specs" that actually live as backend pytest files. Stale trackers cost audit time every cycle — sweep them.
4. **CI/deploy topology contradiction** — `ci.yml`'s deploy job still treats **Railway as primary with a Render fallback**, while CLAUDE.md and `deploy-fly.yml` say Fly primary / Railway standby. Someone will act on the wrong doc during an incident.
5. **`conftest.py` `sys.modules` surgery** — 551 lines including slowapi stubbing and bare↔qualified module-key mirroring; clever, order-dependent, and brittle. Contain it behind documented fixtures.
6. **Archaeology-driven test names** — `test_b_p0_2.py`, `test_e16_*.py`, `test_c2_*.py`: findability is poor; new contributors can't map tests to behavior. Adopt behavior-named files going forward (don't mass-rename).
7. **JWT audience misnomer** — rider and driver access tokens both carry `aud: "spinr:rider"` (`dependencies/__init__.py:83-113`). Not exploitable (role is re-read from DB), but a trap for any future feature that trusts `aud`. Rename/split when convenient.
8. **Corporate allowance RPC wrapper skips quantization + `float()` hop** — `corporate_allowance_service.py:42` lacks the `_money_str()` re-quantize its sibling wallet service has, and `payment_service.py:423-446` routes Decimals through `_f()` to reach it. Harmless today (Postgres NUMERIC(12,2) re-casts), but Postgres rounds half-even, not the mandated HALF_UP — a latent trap for the next caller. Align with `corporate_wallet_service`.
9. **Cosmetic-but-trust-eroding:** dispute resolution push always says "reviewed" — the "approved" branch tests `resolution == "refund"`, a value that can't occur (`disputes.py:293`).

---

## 🧪 Testing & QA (Missing Edge Cases)

**Honest inventory:** ~359 backend test files, and spot-checks confirm they're *real* behavioral tests — the state-machine suite asserts CAS filters **and absence of side-effects** on the losing race branch; payments tests use adversarial inputs; dispatch geometry tests assert actual math. Already covered (don't re-test): double-accept race, WS disconnect mid-ride, surge boundary values incl. the 2.5× cap pin, scheduled-ride DST gaps. Mobile: 21 rider + 19 driver Jest files with contract tests; admin: ~19 Vitest files but **only 3 real Playwright specs**. `loadtest/locustfile.py` is a high-quality two-sided marketplace sim with SLA gates — **never executed** (blocked on missing staging, E1→E2).

**Top missing edge-case tests, by production risk:**
1. Document-expiry sweep firing **during an active ride** — does it strand the ride / corrupt insurance Period 3? Regulatory+insurance exposure. → `test_document_expiry.py`
2. Corporate allowance **concurrent-settlement race** (two rides settling against the same remaining allowance — double-spend past the limit). Wallets have a concurrency test; allowances don't. → `tests/services/test_corporate_allowance_service.py`
3. Payment-retry **exhaustion at the cap** (alert fires once, no infinite loop, terminal payment state). → `test_payment_retry.py`
4. Dispute refund **clamped to captured cents after partial capture** (Stripe hard-rejects over-refunds). → `test_dispute_refund_cents.py`
5. Accept vs offer-timeout **same-instant race** — exactly one path wins; availability + WS events stay consistent. → `test_offer_timeout.py`
6. Insurance-period **rollback on offer timeout** (Period 2 row gets `ended_at`, driver back to Period 1, append-only preserved). → `test_insurance_periods.py`
7. Driver-app **late accept after `ride_offer_expired`** — the client-side 409 UX path. → `driver-app/__tests__/`
8. Admin E2E on **money-touching flows** (corporate billing, refund issuance, surge-override >2.5 justification) — Playwright currently covers login/dashboard/rides only. → `admin-dashboard/e2e/`
9. **WCAG 2.1 AA automation** — a stated regulatory mandate with zero CI coverage (E11); axe is already in devDeps. → wire into Playwright + RN a11y lint. (Client review: booking-critical screens `confirm-pickup.tsx`, `otp.tsx`, driver `RideOfferPanel.tsx` have near-zero a11y props today.)
10. **Any SLA under any load** — every latency target in the CLAUDE.md table is unproven at concurrency; the harness exists. → first `loadtest/` execution once staging is up.

**CI enforcement gaps (the suite is better than its gates):**
- The **A1 gap is real**: only a global `--cov-fail-under=60`; nothing enforces the mandated 90 % on payments/fare or 80 % on rides/dispatch.
- Backend **ruff is `continue-on-error: true`** — lint is decorative on the backend.
- Playwright E2E, coverage-regression, lint-trend, and Gitleaks are all advisory on PRs.
- No post-deploy smoke test (A2) — a bad deploy is still discovered by users.

---

## 📈 Manager's Verdict

**Overall grade: B+ — a disciplined, audit-hardened monolith with one bad outlier and a well-mapped scaling horizon.**

This codebase does not look like a typical pre-launch startup. The backend's replay-safe background loops, atomic claim patterns, Decimal-only money discipline, sanitized error surfaces, and metric coverage that actually matches its documentation are the product of visible, repeated hardening passes — and the sprint/audit trail proves it. The mobile apps enforce error-message hygiene *mechanically* (ESLint + regression tests), which most Series-B companies haven't done. Test volume is high **and** test quality survives adversarial spot-checks.

**The bad news is specific, not systemic.** One unauthenticated endpoint serves government-ID documents (P0 — fix this week, it's a one-line dependency plus an ownership check). The money paths have no P0s but leak at the edges: orphaned Stripe refunds disappear from the ledger, promo-code receipts don't arithmetically reconcile (the 7-year CRA-retained artifact, no less), and the dispute refund cap uses the wrong field, blocking legitimate full refunds. The shared token-refresh code has two genuine concurrency bugs that will present as "app randomly hangs after foregrounding" bug reports at scale. The admin dashboard is the weakest surface by a clear margin — silent blank-table failure modes, unbounded PII fetches, and finance totals silently computed over a truncated 500 rows.

**Scalability is a known, honest ~10× ceiling, not a cliff.** The sync-PostgREST-in-64-threads DB path saturates first (and the gauge to watch it already exists), geospatial matching runs in Python while a finished PostGIS index sits unused, and WS fan-out delivers serially on a single global channel. None of this blocks a Saskatchewan launch; all of it is the post-launch roadmap, and — unusually — most of the fixes are *activation* of infrastructure already built rather than new construction.

**The two systemic weaknesses are enforcement and environments.** The CI gates are softer than the code they guard (60 % global coverage floor vs mandated 90/80 money-path floors; backend lint advisory; E2E advisory), and there is no staging environment — which means no load test has ever run, no SLA has ever been measured, no migration rehearsed, and a deploy failure is discovered by users. The engineering culture is clearly capable of closing these; they're prioritization gaps, not capability gaps.

---

## The Plan

### Week 1 — stop the bleeding (P0/P1, all small)
| # | Action | Where | Effort |
|---|---|---|---|
| 1 | Auth + ownership check on `/api/documents/{file_id}`; retire dead `document_files` fallback | `backend/documents.py` | Hours |
| 2 | Orphan-refund handling in `charge.refunded` (ledger row + admin alert; don't mark processed on failure) | `backend/routes/webhooks.py:897` | Hours |
| 3 | Cap the 401 refresh-retry loop + flush subscribers in `ensureFreshToken` | `shared/api/client.ts` | 1 day |
| 4 | Remove raw GPS/address from estimates + maps-proxy logs | `estimates.py`, `maps_proxy.py` | Hours |
| 5 | Wire OTP lockout into email-OTP verify | `backend/routes/auth.py:638` | Hours |
| 6 | Location-watchdog → force driver offline server-side on GPS loss | `driver-app/hooks/useDriverDashboard.ts` | 1–2 days |

### Weeks 2–3 — correctness + compliance edges
7. Discount line in email receipts (port from `_shared.py:422-430`); fix fallback total. 8. Dispute refund cap → `grand_total`; fix "approved" notification label. 9. Disputes bulk list returns `user_id` only (close B2). 10. Sentry domain tags on dispatch/payment/fare paths + WS delivery-failure metric. 11. Privacy review or reversal of the VIN plaintext migration; narrow the reviewer mock-payment bypass. 12. Delete `cachedClient.ts`, `frontend/` tree, drain-only offline queue, dead `find_nearby_drivers` RPC. 13. Admin dashboard: shared error-state component (kill blank-table pattern), server-side user search in promotions picker, server-side earnings aggregates. 14. Missing edge-case tests #1–6 above. 15. Sweep stale tracker entries (B2 rounding → done, D7, PR #266 attribution) and fix the `ci.yml` Railway-primary drift.

### Month 2 — enforcement + environments (the systemic fixes)
16. Per-module coverage floors in CI (ratchet to 90/80 money paths — closes A1); make ruff blocking; post-deploy smoke test (A2). 17. **Staging environment (E1)** — unblocks: first `loadtest/` execution with SLA gates (E2), OWASP ZAP baseline (E6), migration rehearsal, failover drill (C1). 18. Synthetic monitoring + SLO alerts (E4); Sentry refresh-token-reuse alert rule (C2). 19. Forced-upgrade gate (E3); kill-switch flags for surge/dispatch/promos/corporate (E5). 20. Redis-backed WS rate limit (B4); a11y automation for booking-critical screens (E11).

### Quarter — scaling runway (sequenced by ceiling order)
21. asyncpg/pooled direct Postgres for the 5 hot paths; per-call DB timeouts so threads shed under overload. 22. Activate PostGIS for dispatch (`ST_DWithin` RPC) and surge (`SURGE_SPATIAL_COUNT` flag) — closes D1. 23. Batch the dispatch pipeline (quest `.in_()`, single claim RPC); concurrent WS unicast delivery + channel sharding. 24. Redis Streams for ride events (durable replay). 25. Client `Idempotency-Key` on all money POSTs. 26. External pentest before public launch (E6 second half); CODEOWNERS on money/migration paths (E8).

---

*Review artifacts: five specialist passes over backend security, money paths, architecture/performance, client surfaces, and testing/QA; all findings verified against code as of commit `a6d5102` on 2026-07-24. No code was modified by this review.*
