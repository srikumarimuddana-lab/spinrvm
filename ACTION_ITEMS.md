# Spinr — Production Readiness Action Items

> **How to use this file (for Claude / any AI session):** pick the highest-priority
> `[ ]` item, read its *Files* and *Acceptance* fields, implement it following
> `CLAUDE.md` conventions (≤3 files per subtask, one logical change per commit,
> tests required), then flip it to `[x]` with the PR/commit reference in the
> *Done* column. Do not re-litigate `[x]` items. Companion document with full
> context: `docs/PRODUCTION_READINESS.md`.

_Last updated: 2026-06-09 (branch `claude/rideshare-analysis-optimization-zjhsyb`). Sections: A=launch-gating, B=pre-launch fixes, C=operational, D=post-launch, E=industry-parity._

---

## P0 — Launch gating (code)

### A1. Per-module test-coverage floors for money paths
- [ ] **Status:** open — the single biggest remaining gap
- **Why:** CLAUDE.md mandates ≥90% for `routes/payments.py` + `services/fare_service.py`
  and ≥80% for `routes/rides.py` + `services/dispatch_service.py`; the global floor
  in `backend/pytest.ini` is only 60%.
- **Files:** `backend/pytest.ini`, new tests under `backend/tests/`
- **Approach:** measure current per-file coverage (`pytest --cov --cov-report=term-missing`),
  write tests for the uncovered branches (fare tiers, surge, corporate, promo, refund,
  webhook types), then enforce with `coverage report --fail-under` per path or a
  `ci-guardrails` step. Ratchet, don't big-bang.
- **Acceptance:** CI fails if payments/fare coverage drops below 90% or rides/dispatch below 80%.

### A2. Post-deploy smoke test in CI
- [x] **Status:** done — landed in commit `3bae3db` (before this checklist item
  was last reviewed). Two layers now exist: (1) `deploy-fly.yml` and
  `deploy-backend.yml` each poll `/health` for up to 3 minutes post-deploy and
  automatically roll back to the previous deployment if it never turns
  healthy; (2) `ci.yml`'s dedicated `smoke-test` job (gated on
  `needs: [deploy-backend]`, `main` only) independently re-verifies `/health`
  (200 + DB-ready), `/api/v1/settings` (200 + valid JSON),
  `/api/v1/vehicle-types` (200 + non-empty list), auth middleware
  (`/api/v1/rides` → 401/403, not 500), and the fare service
  (`/api/v1/fares/estimate` → 401/403/422, not 500) — falling back from
  Railway to the Fly domain if the primary is unreachable. `notify-failure`
  sends a Slack alert on any of these jobs failing.
- **Why:** deploys to Fly/Railway succeed or fail silently; a bad deploy is currently
  discovered by users. The smoke script from PR #172 already exists.
- **Files:** `.github/workflows/ci.yml` (`smoke-test`, `notify-failure` jobs),
  `.github/workflows/deploy-fly.yml`, `.github/workflows/deploy-backend.yml`
  (both already had health-poll-and-rollback)
- **Acceptance:** ✅ met — a deliberately broken deploy turns the workflow red within minutes
  (health-poll rollback within 3 min, smoke-test job on top) and pages via Slack.

### A3. PIPEDA breach record register
- [x] **Status:** done — `docs/audit/breach-record.md` exists (created in PR #2222,
  2026-07-25), predating this checklist item's discovery. Row template covers
  discovery/breach timestamps, data categories, affected-individual count,
  RROSH determination, OPC/SGI/affected-individual notification status, root
  cause, fix PR, post-mortem link, and Privacy Officer sign-off — a superset
  of the originally-requested (date, scope, RROSH, notified?, evidence
  location) columns. First row reads "No entries to date" with an explicit
  note that this is the expected pre-launch state, not neglect.
- **Why:** referenced by `docs/runbooks/data-breach.md` but never created; PIPEDA
  requires a 24-month breach record.
- **Files:** `docs/audit/breach-record.md`
- **Acceptance:** ✅ met — template with columns (date, scope, RROSH assessment, notified?,
  evidence location) and a "no entries to date" first row.

### A4. 156 failing backend tests on `main`
- [x] **Status:** ✅ fully complete (2026-07-27) — all 4 buckets cleared, 0
  known backend test failures remain. Bucket 2 (the last holdout —
  `test_wallet.py::TestTransfer`/`TestTopUp`,
  `test_p2_promo_wallet_loyalty.py::TestWalletTopUp`) was resolved per
  explicit product confirmation: wallet-to-wallet transfer is a removed
  feature (no `/transfer` route exists in `routes/wallet.py`) — `TestTransfer`
  was deleted, and `TestTopUp` was rewritten against the current Stripe
  PaymentIntent + EphemeralKey response shape (credit now happens
  asynchronously via the `payment_intent.succeeded` webhook, already covered
  by `test_webhooks_main.py::test_wallet_topup_credits_idempotently_on_reference_id`).
  Fixed across ~22 PRs (#2394 through #2421 and follow-ups), plus several
  genuine production bugs found and fixed along the way (a broken dual-import
  fallback silently dropping an insurance-period audit write; a corporate
  allowance RPC's `p_actor_user_id` parameter silently re-narrowed from
  `TEXT` back to `UUID` by two later migrations, reopening the exact
  `22P02` bug 214 had already fixed). Originally found while triaging PR
  #2377's CI failures (2026-07-26); root-caused 2026-07-26 (see below).
  Confirmed **test drift, not a product regression** — production code
  changed correctly; tests were never updated to match. Full local suite
  run (2026-07-27): `4667 passed, 8 skipped, 1 xfailed, 0 failed`.
- **Root cause breakdown (ranked by likely share of the 156):**
  1. **Orphaned `patch()` targets after module splits (likely >half of the 156).**
     `routes/drivers.py` → `routes/drivers/` package, `routes/rides.py` →
     `routes/rides/` package, and `routes/wallet.py` logic partially extracted
     into `repositories/wallet_repo.py`. Tests still `patch()` symbols at their
     old location (e.g. `routes.drivers.set_presence`, now `utils.driver_presence`;
     `routes.wallet.wallet_increment_balance`, now `repositories/wallet_repo.py:31`
     re-exported via `db_supabase.py:300,315`) — `AttributeError` on `patch()`
     fails the test before any assertion runs. Confirmed-dead patch targets found
     in `test_coverage_rides.py` (128 tests), `test_drivers_extended.py` (81
     tests), `test_wallet.py`, `test_p2_promo_wallet_loyalty.py`,
     `test_dispatch_cascade.py`, `test_dispatch_presence_failopen.py`.
     **Fix:** mechanical sweep re-pointing each `patch()` string at the module
     that now actually owns the symbol — no logic changes, lowest-risk bucket to
     clear first and likely clears well over half the 156 in one pass.
  2. **Wallet endpoints genuinely changed shape.** `POST /wallet/transfer` was
     removed entirely (`routes/wallet.py` has no `/transfer` route — the 404s
     in `TestTransfer` are correct current behavior, not a bug). `POST
     /wallet/top-up` was rewritten to create a Stripe PaymentIntent +
     EphemeralKey instead of crediting the balance synchronously — credit now
     happens via the `payment_intent.succeeded` webhook (`wallet.py:144-227`).
     Old tests assert the pre-rewrite synchronous-credit contract.
     **Fix:** delete `TestTransfer` (dead feature) or replace with a real
     transfer test if the feature is coming back; rewrite `TestTopUp` against
     the PaymentIntent-creation response shape, add a webhook-level test for
     the actual credit path.
  3. **New tax computation exhausts fixed-length mocks.**
     `_compute_subscription_tax` (`routes/drivers/subscriptions.py:237`) is new
     code that adds 2 extra `db_supabase.find_one` calls in the one-off
     subscription-activation path (`subscriptions.py:1285`) and the
     `invoice.paid` webhook handler (`webhooks.py:1329`). Old tests supply a
     3-element `side_effect` list; the 4th call raises `StopAsyncIteration`.
     Sibling tests on the *recurring* branch (which skips this new code path)
     still pass, confirming the new calls are additive, not broken.
     **Fix:** add 2 more `find_one` mock responses (drivers row, service_areas
     row) to each affected `side_effect` list.
  4. **New guard clauses the old fixtures don't satisfy** — each deliberate,
     correct behavior:
     - `surge_engine.py:263` added a `surge_enabled` backstop; old
       `TestRecalculateAllSurges` fixtures omit that field so every area is
       skipped. Fix: add `"surge_enabled": True` to the fixtures.
     - `verify_subscription_session` now re-reads status after activation
       (a newer sibling test, `test_verify_session_superseded_returns_superseded`,
       already models this correctly) — the old test's mock returns a stale
       `"pending"` status via `return_value` instead of a `side_effect`
       sequence. Fix: match the newer sibling test's mocking pattern.
     - `_WSPubSub.active` (`utils/ws_pubsub.py:72-83`) now also requires
       `_pubsub is not None` (deliberate reconnect-safety fix, documented in
       its own docstring) — old test only stubs `_redis`/`_task`. Fix: also
       stub `_pubsub` in the test fixture.
- **Files:** `backend/tests/test_wallet.py`, `backend/tests/test_webhooks_main.py`,
  `backend/tests/test_spinr_pass_subscription.py`, `backend/tests/test_coverage_rides.py`,
  `backend/tests/test_drivers_extended.py`, `backend/tests/test_dispatch_cascade.py`,
  `backend/tests/test_dispatch_presence_failopen.py`, `backend/tests/test_utils_extended.py`,
  `backend/tests/test_websocket_token_revocation.py`, `backend/tests/test_p2_promo_wallet_loyalty.py`
  (start with bucket 1 above; a fresh `pytest -v` pass is still needed to confirm
  the exact full list — this breakdown was derived from a sample of 17 of the 156
  signatures plus a static `patch()`-target scan, not a full local run)
- **Approach:** fix bucket 1 first (pure patch-target sweep, no behavior
  questions, respects ≤3-files-per-subtask) to see how much it clears, then
  buckets 2–4 in order. Do not skip/xfail to turn CI green; fix or delete each
  test on its merits. Bucket 2 (`TestTransfer`) needs a product decision first
  — confirm wallet-to-wallet transfer is actually a dead/removed feature before
  deleting its tests, rather than assuming.
- **Acceptance:** ✅ met — `pytest` reports 0 failures on this branch (full
  suite: 4667 passed, 8 skipped, 1 xfailed); CI Guard Rails coverage gate is
  meaningful again once this merges to `main`.

### A5. PyJWT HIGH-severity CVE-2026-48526 (auth bypass) in backend image
- [x] **Status:** done — fixed via CR-2026-004, PR #2474 (merged 2026-07-27,
  squash sha `0026612`). PyJWT bumped 2.12.1→2.13.0 (part of a 13-package
  dependency bump also clearing the overlapping `G6 · Trivy container scan`
  findings). Full backend suite run before/after with byte-for-byte identical
  failure sets; targeted auth/JWT/MFA/OTP/token pass (542 tests) also
  unaffected. See `docs/change-log/2026-07-27-cr-2026-004-backend-dep-bump.md`.
- **Why:** `docker-image-scan` job flags `PyJWT==2.12.1` for `CVE-2026-48526`, an
  authentication-bypass-via-forged-JWT vulnerability, fixed in PyJWT `2.13.0`.
  Given CLAUDE.md's JWT trust model (admin JWTs are fully trusted on role/email/
  modules claims), an unpatched JWT-forgery CVE in the dependency stack is worth
  fixing ahead of its normal priority, independent of the specific PR that
  surfaced it (a docs-only change did not introduce this).
- **Files:** `backend/requirements.txt` (or `requirements.in`), regenerate
  `backend/requirements-locked.txt` via
  `pip-compile --generate-hashes --resolver=backtracking`
- **Approach:** bump PyJWT to `>=2.13.0`, regenerate the hash-locked requirements
  file per `docs/runbooks/dependency-update.md`, run the full auth test suite
  (`backend/tests/test_auth.py`, `test_admin_mfa_enforcement.py`,
  `test_admin_privilege_escalation.py`, `test_p3_admin_jwt_modules.py`) to confirm
  no behavior change, then re-run the Trivy image scan to confirm the finding clears.
- **Acceptance:** `docker-image-scan` job passes with 0 HIGH/CRITICAL findings for
  PyJWT; all auth tests still pass.

### A6. Flaky backend tests: `test_no_double_accept`, `test_ranks_by_vector_similarity_with_no_lexical_overlap`
- [x] **Status:** fixed (2026-07-27) — both tests rewritten to remove their
  timing/ordering assumptions; each passed 20/20 consecutive local runs after
  the fix (0/20 before, on the unmodified originals). PR: (branch
  `claude/a6-flaky-test-fixes`). Found while driving PR #2421 (A4 closure) to
  green; confirmed flaky via two `backend-test` CI runs on an identical commit
  producing different results (2 failed / 4665 passed, then 1 failed / 4666
  passed) plus local isolation runs. Neither test's file was touched by #2421
  — pre-existing flakiness, not a regression from that PR.
- **Why (root causes):**
  - `tests/test_rides.py::test_no_double_accept` raced two `asyncio.gather`
    coroutines against `patch("backend.routes.drivers._deps.db.update_one",
    AsyncMock(side_effect=[accepted_ride, None]))` — a fixed-order list. Real
    concurrent scheduling under `asyncio.gather` does not guarantee call order
    matches list order, so whichever coroutine's `update_one` call landed
    second could pull the `accepted_ride` slot meant for the "winner," or
    exhaust the list before both calls resolved, raising
    `StopAsyncIteration` and failing with `AttributeError:
    'StopAsyncIteration' object has no attribute 'status_code'`.
  - `tests/test_ai_tools_support.py::TestSearchFaqsSemantic::test_ranks_by_vector_similarity_with_no_lexical_overlap`
    persists freshly-embedded FAQ rows via a genuine fire-and-forget
    `asyncio.create_task` in `ai/tools_support.py::_schedule_persist` (never
    awaited by the caller — deliberate, so the user-facing tool call doesn't
    block on it). The test drained it with a fixed `await asyncio.sleep(0.05)`
    then asserted `update.assert_awaited()` — under CI scheduling load the
    background task could still be pending after 50ms, failing with
    `AssertionError: Expected mock to have been awaited`.
- **Fix:**
  - `test_no_double_accept`: replaced the fixed-order `side_effect` list with
    an `update_one`/`find_one` fake that models the real DB's "first caller
    whose conditional UPDATE still matches wins" semantics — an
    `asyncio.Lock`-guarded dict keyed by claim state, not call order. The test
    now exercises the actual race-handling logic instead of coincidental
    scheduling, and passes regardless of which driver's coroutine happens to
    run first.
  - `test_ranks_by_vector_similarity_with_no_lexical_overlap`: patched
    `tools_support.asyncio.create_task` to capture the task the production
    code creates, then `await`ed it directly after the tool call instead of
    sleeping — draining the background write is now deterministic, not
    time-based.
- **Files:** `backend/tests/test_rides.py`, `backend/tests/test_ai_tools_support.py`
- **Acceptance:** ✅ met — both tests pass 20/20 consecutive local runs. CI
  confirmation (≥3 consecutive green `backend-test` runs) pending merge.

## P1 — Fix before launch (code)

### B1. `track_driver_online` accepts raw GPS for third-party analytics
- [x] **Status:** done — geohash-string-only signature; lat/lng dict raises
  TypeError, non-geohash string raises ValueError; contract pinned in
  `backend/tests/test_analytics_geohash.py`
- **Files:** `backend/utils/analytics.py:346`
- **Approach:** change the signature to accept a geohash string only; never accept
  or forward a lat/lng dict to Mixpanel/Amplitude. Add a test pinning the contract.
- **Acceptance:** no analytics interface accepts raw coordinates; test added.

### B2. Disputes store full legal names + RLS too broad + rounding
- [ ] **Status:** open (3 related fixes, one migration + one route change)
- **Files:** `backend/routes/disputes.py:188` (full name in response/at rest),
  `backend/routes/disputes.py:227` (`int(...)` without `_round()` floors refund cents),
  new migration (next free slot — check `backend/migrations/`) replacing the
  `FOR ALL TO authenticated` policy from `10_disputes_table.sql` with enumerated
  SELECT/UPDATE + append-only trigger (pattern: `audit_logs`, migration 51).
- **Acceptance:** disputes responses carry `user_id`/display alias only; refund cents
  use `int(_round(amount * 100))`; DELETE on disputes blocked at DB level.

### B3. Driver location-update hot path (perf + Maps spend)
- [x] **Status:** done — branch `claude/eager-franklin-69ta0w` (3 commits + completion-flush fix)
- **Files:** `backend/routes/websocket.py`, `backend/utils/breadcrumbs.py`,
  `backend/utils/maps_eta.py`, `backend/utils/breadcrumb_buffer.py`
- **Done:**
  1. `resolve_active_rides_cached` — Redis 5s TTL, empty results cached, soft
     degrade (tests: `test_active_ride_cache.py`);
  2. ETA movement gate >100m, ride-scoped `driver:{id}:last_eta_loc`, 120s upper
     bound (tests: `test_maps_eta_movement_gate.py`);
  3. breadcrumb batching 10 points / 10s / ride-change, flush on WS disconnect
     and at complete_ride before trail aggregation (tests: `test_breadcrumb_buffer.py`).

### B4. WS per-user rate limit is per-replica only
- [ ] **Status:** open (acknowledged P3 in code; formalized here)
- **Files:** `backend/socket_manager.py:27-37`
- **Approach:** promote the message counter to Redis (`INCR` + `EXPIRE` 1s window)
  with in-process fallback when Redis is absent.
- **Acceptance:** cap holds at N msg/s per user across all replicas.

### B5. Migrate AI place lookup to Places API (New) with hard locationRestriction
- [ ] **Status:** open — follow-up to the AI location/pricing incident fixes
  (branch `claude/app-location-pricing-bugs-gxgk3z`)
- **Why:** `backend/ai/tools_booking.py` still uses legacy Geocoding + Text
  Search. The incident fixes added a soft `bounds` bias and nearest-first
  sorting, but the rider app's `utils/google_places_new.py` path gets a HARD
  `locationRestriction` circle + `origin` ranking — full parity closes the
  remaining "Google returns only far matches" gap. Related deferred items:
  a hard estimate_token price-lock across the chat→card gap, a blocking
  surge sheet on the AI confirm card (parity with `ride-options.tsx`), a
  structured (non-prose) payload for quote-card taps, and Maps budget
  accounting for `places_text_search` + the fare Directions calls (neither
  is a priced SKU in `utils/maps_budget.py`, so real spend is undercounted).
- **Files:** `backend/ai/tools_booking.py`, `backend/utils/google_places_new.py`,
  `backend/utils/maps_budget.py`
- **Acceptance:** AI place lookups never return a candidate outside the bias
  circle without an explicit flag, and `estimate_today_usd` counts every
  Google call the platform makes.

### B6. Measure Directions latency and re-tune the fare-estimate wait
- [ ] **Status:** open — follow-up to the AI location/pricing incident fixes
  (branch `claude/app-location-pricing-bugs-gxgk3z`)
- **Why:** the billed distance basis must not depend on scheduler timing, so
  `estimates._PRICING_ROUTE_WAIT_S` is derived as `DIRECTIONS_TIMEOUT_S + 0.5`.
  That makes the Directions HTTP timeout the sole lever on worst-case estimate
  latency, and CLAUDE.md pins fare estimate P95 at **300 ms**. The timeout was
  set to 1.5 s (wait 2.0 s) on judgement, not data — nobody has measured the
  real Directions latency distribution, so we don't know how much of the
  road-route benefit a tighter timeout would give up.
- **Action:** record a `spinr_fare_directions_duration_ms` histogram, then pick
  the timeout from the observed p99 rather than by feel. If the p99 sits well
  under 1.5 s, tighten both constants; if Directions is routinely slower than
  the SLA allows, the honest fix is pre-warming/caching routes for common
  origin-destination pairs, not a longer wait.
- **Files:** `backend/routes/rides/_shared.py`, `backend/routes/rides/estimates.py`,
  `backend/utils/metrics.py`
- **Acceptance:** the timeout is justified by a recorded latency distribution,
  and `test_pricing_wait_stays_within_the_estimate_latency_budget` reflects the
  chosen ceiling.

### B7. Give service areas a real locality so the geocode can be hard-filtered
- [ ] **Status:** open — follow-up to the AI location/pricing incident fixes
- **Why:** the Geocoding API treats `bounds` as a *soft* hint but `components`
  as a **hard** filter. `components=locality:Regina` would make it impossible
  for a Regina query to resolve to a same-named street in another city — the
  strongest available fix for cross-city mis-resolution. It is not wired up
  because `service_areas` has no city column, only `name`, which is a display
  label ("Regina Metro"); a wrong locality returns `ZERO_RESULTS` and breaks
  lookups outright, so a filter built on it is worse than none.
- **Action:** add a `locality` column to `service_areas` (migration + admin
  field), backfill for existing areas, then pass it as `components` in
  `_lookup_place_candidates` with an unfiltered retry on `ZERO_RESULTS`.
- **Files:** `backend/migrations/NNN_service_areas_locality.sql`,
  `backend/ai/tools_booking.py`, admin service-area editor
- **Acceptance:** a numbered street address in a covered city can never
  resolve to another city, and an unknown locality degrades to today's
  behaviour rather than to zero results.

### B8. Economy and XL quote identical fares (per-vehicle-type pricing unseeded)
- [ ] **Status:** open — observed in an AI-assistant quote card, 2026-07-26
- **Why:** a rider-facing quote showed **Economy and XL at the same price**
  ($23.52 → $13.52 after promo), both "2 min (0 km) away". Vehicle types are
  supposed to differ via `base_fare` / `per_km_rate` / `per_minute_rate`;
  identical output is the signature of both falling back to `DEFAULT_FARE`
  (`backend/services/fare_service.py:32-34`) because per-vehicle-type pricing
  rows are missing. Riders paying XL money for Economy pricing (or vice versa)
  is a revenue and trust problem, and it makes the vehicle picker meaningless.
  "Nearest driver 0.0 km away" in the same card is also suspect.
- **Action:** confirm against production `service_areas.vehicle_pricing`
  (migration 80) whether rows exist per vehicle type; if they do, trace why the
  fare service falls through to `DEFAULT_FARE`. Needs production pricing data —
  not diagnosable from the repo alone.
- **Files:** `backend/services/fare_service.py`,
  `backend/migrations/80_service_areas_vehicle_pricing.sql`
- **Acceptance:** each vehicle type quotes from its own configured rates, and a
  missing pricing row surfaces loudly instead of silently defaulting.

### B9. Address+coordinate pairs are stored server-side with zero consistency validation
- [ ] **Status:** open — follow-up to the Glide Crescent wrong-pin incident
- **Why:** the client-side carriers of mismatched pairs are fixed (recents v2,
  search-screen pin integrity, map-pick label binding), but the backend still
  accepts and replays unvalidated pairs:
  - `POST /addresses` (`backend/routes/addresses.py:29-42`) stores any
    `{address, lat, lng}` triple — no address↔coordinate cross-check, no
    service-area check, no expiry. Saved places render in the destination
    picker and are trusted verbatim.
  - `POST /favorites/from-ride/{ride_id}` (`backend/routes/favorites.py:135-158`)
    copies a ride's stored pair verbatim into a permanent favorite — a poisoned
    ride row gets laundered into a never-expiring replay source. (Unwired in
    rider-app today, but a live trap.) Its dedupe also compares `pickup_lat`
    and `dropoff_lat` only — longitude never (`favorites.py:71-72`).
  - `CreateRideRequest` (`backend/schemas.py:426-433`) persists client-supplied
    address strings beside coordinates with no cross-field validation, making
    the rides table itself a durable record of whatever pair the client sent.
- **Action:** store `place_id` with saved addresses and re-resolve on save;
  geocode-verify pairs at write time (reject > ~1 km mismatch); fix the
  favorites dedupe to compare both axes of both endpoints.
- **Files:** `backend/routes/addresses.py`, `backend/routes/favorites.py`,
  `backend/schemas.py`
- **Acceptance:** no endpoint persists an address whose stored coordinate is
  more than ~1 km from where that address geocodes.

## P2 — Operational (no/low code — needs a human with dashboard access)

### C1. Failover drill — Railway ↔ Fly
- [ ] **Status:** never exercised
- **Action:** run the cutover in `docs/runbooks/railway-fly-failover.md` end-to-end
  in a low-traffic window; record actual timings and surprises back into the runbook.

### C2. Sentry alert rule — refresh-token theft tripwire
- [ ] **Status:** open (~5 min in Sentry UI, no code)
- **Action:** alert on message `REFRESH TOKEN REUSE DETECTED` → email/PagerDuty.
  The loguru→Sentry bridge already delivers it; it just needs a rule.

### C3. Production env sweep on Fly/Railway
- [ ] **Status:** partially done (SENTRY_DSN deployed via Fly Sentry extension — verify
  boot log shows "Sentry SDK initialized for error monitoring")
- **Action:** confirm `SENTRY_DSN`, `REDIS_URL`/`RATE_LIMIT_REDIS_URL`/`WS_REDIS_URL`,
  Firebase App Check enforcement, and `ENV=production` are set on **both** providers
  (standby drifts silently).

### C4. Staff MFA rollout comms
- [ ] **Status:** code shipped; people not yet notified
- **Action:** tell all admin staff that the next login forces authenticator enrollment
  (ADMIN_MFA_ENFORCED). Ensure ≥2 active super_admin accounts exist for the
  lost-phone reset path.

## P3 — Post-launch backlog (tracked, not gating)

- [ ] **D1. PostGIS surge query** — `surge_engine.py` caps at 500 drivers with Python
  point-in-polygon; move the count server-side when driver count approaches the cap.
- [ ] **D2. Distributed tracing** — request-ID propagation exists (`X-Request-ID`);
  full OpenTelemetry only if multi-replica latency debugging becomes painful.
- [ ] **D3. Driver destination mode** — biggest driver-retention feature gap vs industry.
- [ ] **D4. Driver heatmap UI** — `utils/demand_forecast.py` exists server-side; surface
  it in the driver app.
- [ ] **D5. In-app VoIP calls** — Twilio Proxy PSTN masking already covers the need;
  VoIP is a cost/quality upgrade.
- [ ] **D6. Read-only root filesystem** — blocked on host migration off Railway.
- [ ] **D7. Admin analytics Redis cache** — 5-min cache on cancellation-breakdown
  (`routes/admin/analytics.py:72`), drops dashboard DB load ~98%.
- [ ] **D8. Payment-retry admin alert via WS broadcast** — replace per-admin push loop
  (`utils/payment_retry.py:80`) with one `broadcast_to_admins`.

## P4 — Industry-parity good-to-haves (verified missing 2026-06-09)

_Not launch-gating, but every mature platform at this stage has them. Ordered by
how much they de-risk a public launch._

- [ ] **E1. Staging environment** — deploys currently go `main` → production
  (Fly + Railway) with no intermediate environment. Stand up a staging Fly app +
  throwaway Supabase project with synthetic data; point a `staging` branch or
  manual workflow at it. Prereq for E2, E4, and safe migration rehearsal.
- [ ] **E2. Marketplace load/simulation testing** — harness BUILT on branch
  `claude/eager-franklin-69ta0w` (`loadtest/locustfile.py` + runbook with
  breaking-point register): rider+driver bots, real dispatch matchmaking, WS
  GPS pings, SLA gates from the CLAUDE.md table. **Execution still open** —
  blocked on E1 (no staging env). First run: seed bot accounts per
  `loadtest/README.md`, run the ramp scenario, record the breaking point.
- [ ] **E3. Forced-upgrade gate for mobile apps** — no minimum-supported-version
  check exists. Old app binaries in the wild will eventually hit removed/changed
  APIs. Add `min_supported_version` to `app_settings`, a version header from the
  apps, a 426-style backend response, and an "update required" screen in both apps.
  Cheap now, impossible to retrofit onto clients that are already old.
- [ ] **E4. Synthetic monitoring + SLO alerting** — nothing external probes the
  platform; a total outage is currently discovered by users. Add an external
  monitor (Checkly/UptimeRobot/Grafana synthetic) hitting `/health`, auth, and
  fare-estimate every minute from outside, alerting to PagerDuty. Tie alert
  thresholds to the CLAUDE.md SLA table (SLO + error budget).
- [ ] **E5. Kill switches / feature flags** — `app_settings` covers config, but
  there are no documented kill switches for the risky subsystems (surge engine,
  scheduled dispatch, promo redemption, corporate billing). Add boolean flags
  checked at the top of each loop/path + admin UI toggles, so a misbehaving
  subsystem can be disabled in seconds without a deploy.
- [ ] **E6. Pre-launch DAST + third-party pentest** — SAST/Semgrep run in CI, but
  nothing exercises the running app (OWASP ZAP baseline scan against staging on a
  schedule), and a payments+PII platform should have one external penetration
  test before public launch. Budget item; book it.
- [ ] **E7. Backup-restore drill** — `docs/runbooks/pitr-restore.md` exists but
  (like the failover runbook) has never been exercised. Restore a Supabase PITR
  snapshot into a scratch project, verify row counts + a sample ride lifecycle,
  record actual RTO in the runbook. A backup is only real after a restore.
- [ ] **E8. CODEOWNERS + review routing** — no `.github/CODEOWNERS`. Route
  `backend/routes/payments*`/`services/fare*`/`migrations/` to designated
  reviewers so money/schema changes can't merge on a drive-by approval.
- [ ] **E9. Blameless postmortem template** — `data-breach.md` has one for
  breaches; generalize to `docs/templates/postmortem.md` (timeline, impact,
  5-whys, action items with owners) and link it from the incident runbooks.
- [ ] **E10. License compliance scan** — dependency *vulnerability* audit exists;
  add license checking (`pip-licenses` + `license-checker` in CI, fail on
  GPL/AGPL in shipped surfaces). Matters for SOC 2 and any future diligence.
- [ ] **E11. a11y checks in CI** — WCAG 2.1 AA is a stated regulatory mandate and
  axe is already in admin-dashboard devDeps, but nothing runs it in CI. Wire
  axe into the Playwright E2E suite for the customer-facing surfaces.
- [ ] **E12. On-call & escalation policy doc** — PagerDuty is referenced by
  alerts, but there is no rotation/escalation/severity-matrix document. One page:
  who is paged, when P0 vs P1, response-time expectations (support SLA says <2h P1).

## Recently completed (do not redo)

| Item | Where |
|---|---|
| `/auth/refresh` reported 30-day `access_expires_at` (real TTL 15 min) | `b5648ba` |
| SOS accepts expired-but-signature-valid token (`get_current_user_allow_expired`) | `rides.py:4178` |
| `confirm_payment` raw dict → Pydantic model | `routes/payments.py:303` |
| Dispatch pushes moved off the request path (<2s offer SLA) | `e9283fc` |
| Estimate polyline fetch overlapped with fare work (<300ms SLA) | `d322709` |
| Partial recency index for estimate driver page | `5788367` |
| Admin JWT error-detail leaks fixed + MFA challenge audience pinned | `e3281ed` |
| MFA enforced for all staff logins (`ADMIN_MFA_ENFORCED`, enroll-scoped token) | `test_admin_mfa_enforcement.py` |
| Super-admin MFA reset (lost phone) + staff-page UI | `884b091`, `664d195` |
| TOTP secrets / backup-code hashes stripped from staff list/get | `664d195` |
| Production boot without `SENTRY_DSN` logs unmissable ERROR | `server.py` |
| All 6 sprint P0s + P1/P2 audit findings | `.claude/context/sprint-current.md` |
