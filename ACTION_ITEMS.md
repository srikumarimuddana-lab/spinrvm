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
- [ ] **Status:** in progress (2026-07-27) — measured actual current per-file
  coverage (full local suite run, `coverage.xml`), which changes the shape of
  this item from what it originally assumed:
  - `routes/payments.py`: **90.72%** — already meets the 90% target.
  - `services/fare_service.py`: **99.36%** — already meets the 90% target.
  - `services/dispatch_service.py`: was 75.86%, **now 92.53%** after adding
    10 tests (`tests/services/test_dispatch_service.py`) covering the
    previously-untested Spinr Pass gate block in
    `DispatchService.find_candidate_drivers` — required-area subscription
    filter, parent-area inheritance, expired-subscription handling, the daily
    ride-allowance/quota filter, and both fail-open exception paths (quota
    lookup failure, general pass-filter DB error). Meets the 80% target.
  - `routes/rides.py` no longer exists as a single file — the god-file split
    (see `backend/CLAUDE.md` / `docs/refactors/god-file-split.md`) replaced it
    with a `routes/rides/` package, and CLAUDE.md's target was never updated
    to reflect that. Per-file coverage in that package was **highly uneven**:
    `lost_found.py` 25% (now **100%** — see below, meets target),
    `receipts.py` 58.3% (now **100%** — see below, meets target),
    `matching.py` 64.7% (now **79.41%** combined — PR #2557 merged, this PR
    adds the remaining increment; see below — just under the 80% target),
    `lifecycle.py` 65.1% (now **87.88%** — meets target),
    `booking.py` 65.7% (now **84.54%** — meets target, PR #2559),
    `queries.py` 69.2% (now **92.20%** — meets target, PR #2544),
    `estimates.py` 71.0% (now **93.99%** — meets target, PR #2552),
    `cancellation.py` 71.0% (now **95.06%** — meets target, PR #2555),
    `rides/payments.py` 79.5%.
  - `routes/rides/lost_found.py`: was 25%, **now 100%** after adding 10 tests
    (`tests/test_lost_found.py`) covering the 404/403/400 guard clauses,
    category-validation fallback, the driver-notification success path (push
    sent + status updated to `driver_notified`), and three
    notification-skip/failure branches. Full backend suite re-run after:
    4697 passed, 8 skipped, 1 xfailed, 0 failed — no regressions.
  - `routes/rides/lifecycle.py`: was 65.1%, **now 87.88%** after adding 23
    tests (`tests/test_coverage_rides.py`) covering the ride state-machine
    functions' previously-untested guard clauses (404/403/400/409 branches
    across `simulate_driver_arrival`, `rider_start_ride`,
    `rider_complete_ride` — including both atomic-transition race-lost 409
    paths) and their fail-open exception paths (insurance-period audit write,
    daily-quota check, driver-earnings snapshot, admin broadcast, quest
    scheduling, quota-exhaustion driver notification). Meets the 80% target.
    Remaining gap: the ride-incentive-claim happy path (lines 208-230,
    a Supabase query-builder chain) and a small WS/push branch in
    `rider_start_ride` — left uncovered, lower priority than the other
    `routes/rides/` files below.
  - `routes/rides/matching.py`: was 64.7%, then 72.48% (25 tests,
    `tests/test_offer_timeout.py` + `tests/test_p0_ship_blockers.py`,
    covering the file's smaller, self-contained functions:
    `create_demo_drivers` (deprecated no-op), `_dispatch_retry`'s
    attempt-cap/ride-left-searching guards and error-backoff reschedule,
    `process_expired_offer`'s claim-lost-returns-False path, the
    auto-offline-at-threshold branch, and its two fail-open exception paths
    (Redis skip-key write, WS notify), `_batch_offer_timeout_handler`'s two
    early-returns and settings-fetch-failure fallback plus its outer
    exception handler, and `ride_search_timeout`'s pre-auth-release
    success/failure branches, the attribution-column-fallback retry, the
    guest-booking SMS branch, and its outer exception handler), **then
    76.05%** (PR #2557, merged) after adding 8 more tests
    (`tests/test_dispatch_match_attempt_branches.py`) covering
    `_match_driver_to_ride_attempt`'s (lines 151-933, ~780 lines) most
    self-contained guard clauses and fail-open/fail-closed exception paths:
    the stale-ride-status skip (dispatch already progressed past
    `searching`), the subscription filter's fail-**closed** exception
    (empties the pool — a subscription-gated area must never leak an offer
    to a non-subscriber on a DB hiccup), the daily-quota filter's fail-**open**
    exception (must NOT drop the pool — a transient quota-lookup error can't
    strand every ride), the cascade pool's own subscription sub-filter
    (drops non-subscribed cascade/upgrade-type drivers, and its own
    fail-closed exception), the cascade lookup's outer exception (non-fatal —
    falls through to the no-eligible-drivers retry), the
    `ride_offers`-insert failure (releases the claimed driver back to
    available *before* re-raising, so a transient insert failure can't
    strand a driver as claimed-but-never-offered), the final
    no-eligible-drivers-after-all-filters retry, and the
    no-drivers-could-be-claimed early return (claim lost to a race). **This
    PR** adds a further increment on top of that 76.05% baseline (5 tests,
    `tests/test_dispatch_notify_loop_branches.py`) covering the
    ETA-ranking/batch-claim/parallel-enrichment/per-driver-notify block
    (~lines 650-930): a full happy-path test building the WS
    `new_ride_assignment` payload end-to-end (quest progress, active
    incentive, signed offer-card URL, FCM push all populated) plus the
    ETA-ranking Distance-Matrix-failure fallback and the notify loop's three
    fail-open exception paths (quest-progress lookup, offer-card URL
    signing, FCM push spawn). **Combined with PR #2557: 76.05% → 79.41%** —
    just under the 80% target. Full local suite confirmed 0 new/different
    failures (4821 passed, same known 9 A7 `test_ai_tools_booking.py`
    failures). Remaining gap (~0.6pp) is scattered across the file's
    smallest remaining uncovered branches — deferred as low-priority; not
    worth a dedicated follow-up PR for under a percentage point.
  - `routes/rides/receipts.py`: was 58.3%, **now 100%** after adding 15 tests
    (`tests/test_coverage_rides.py`) covering both endpoints end-to-end:
    `get_ride_receipt`'s no-driver-shows-"Unknown Driver" branch, the
    vehicle-type lookup, the corporate-account "Corporate Account" payment
    method + name branch, the cancelled-ride cancellation-fee sum, the
    fare-lock snapshot path (including the synthesized tip line when the
    snapshot predates the tip), and the settings-lookup-failure fallback to
    the dynamic (non-locked) rebuild; plus `email_ride_receipt` end-to-end
    (404/403/400 guards, success, and the 503-on-send-failure path). Meets
    the 80% target.
  - `routes/rides/booking.py`: was 65.7%, **now 77.84%** after adding 21
    tests (new `tests/test_ride_insert_and_dispatch_prep.py`, plus 5 more in
    `tests/test_ride_preauth_booking.py`) covering the file's two
    previously-fully-uncovered helpers — `_insert_ride_with_code` (the
    PGRST204 pre-migration-40 fallback, both constraint-specific branches —
    `rides_one_active_per_rider` → 409, `idx_rides_rider_idempotency_key`
    replay → returns the winning ride so the caller skips re-running
    dispatch/promo side-effects — the ride_code-collision retry, and the
    3-attempt-exhaustion → 503 path) and `_prep_and_dispatch` (the
    post-booking background pipeline: pickup road-snap, server polyline
    computation, dispatch kickoff, and its fail-open behavior at every
    stage — a snap/polyline/dispatch failure must never propagate, since
    this runs as a fire-and-forget background task) — plus 3 previously-
    missed branches in the already-tested `_preauthorize_ride_card` /
    `_attach_preauthorized_hold` pre-auth helpers (fare-only retry
    requires-SCA, fare-only retry ops-failure degrade, PI-reuse-lookup
    failure fail-open), **now 84.54%** (PR #2559) after 10 more tests
    (`tests/test_create_ride_remaining_branches.py`) covering `create_ride`'s
    remaining guard clauses (service_areas fetch failure, insufficient
    wallet balance, corporate policy check, work_profile corporate block,
    SCA first-leg early return, idempotency-key replay, calculate_all_fees
    failure). Meets the 80% target.
  - `routes/rides/queries.py`: was 69.2%, **now 92.20%** (PR #2544) — see
    the PR for detail; meets the 80% target.
  - `routes/rides/estimates.py`: was 71.0%, **now 93.99%** (PR #2552) after
    adding 12 tests (`tests/test_ride_estimate_branches.py`) covering
    `compute_ride_estimates`'s geofence guards (pickup/dropoff/stop
    `OUTSIDE_SERVICE_AREA`), malformed driver-row skip reasons, the
    vehicle-cascade upgrade fallback, `calculate_all_fees` failure → 503,
    the Directions route fetch/await fail-open paths, and
    `_track_price_search`. Meets the 80% target.
  - `routes/rides/cancellation.py`: was 71.0%, **now 95.06%** after adding
    13 tests (new `tests/test_ride_cancellation_branches.py`) covering
    `cancel_ride_rider`'s request-body JSON-parse-failure fallback to the
    query `reason`, the atomic-claim-lost 409 (driver started the trip in
    the race window), the pre-auth-release fail-open (both the
    Stripe-call-fails and the succeeds-and-marks-`auth_status=released`
    branches), a partial wallet-fee collection (charges less than the full
    fee, logs the shortfall), the outer fee-computation exception fail-open
    (settings/area/fee-calc failure must still release + notify the
    driver), the attribution-column write's PGRST204 retry-minimal
    fallback, the post-write verification re-read's own exception path
    (still correctly raises the "did not persist" 500), and the batch
    pending-`ride_offers` cleanup loop (cancel + release + notify each
    offered driver, plus its own fail-open); and `cancel_scheduled_ride`'s
    attribution-column fallback, the pre-dispatch-claim-lost-to-a-race
    fall-through to the full `cancel_ride_rider` path, and the
    claim-lost-and-now-terminal → 400 branch. Meets the 80% target.
- **Why:** CLAUDE.md mandates ≥90% for `routes/payments.py` + `services/fare_service.py`
  and ≥80% for `routes/rides.py` (now the `routes/rides/` package) +
  `services/dispatch_service.py`; the global floor in `backend/pytest.ini` is
  only 60%.
- **Files:** `backend/pytest.ini`, new tests under `backend/tests/` — done so
  far: `backend/tests/services/test_dispatch_service.py`,
  `backend/tests/test_lost_found.py`,
  `backend/tests/test_coverage_rides.py` (lifecycle + receipts + queries
  functions), `backend/tests/test_rider_stats_empty.py` (queries.py),
  `backend/tests/test_offer_timeout.py` + `backend/tests/test_p0_ship_blockers.py`
  (matching.py's smaller functions), `backend/tests/test_dispatch_match_attempt_branches.py`
  (PR #2557, matching.py's guard/fail-open branches),
  `backend/tests/test_dispatch_notify_loop_branches.py` (this PR,
  matching.py's ETA-ranking/enrichment/notify-loop branches),
  `backend/tests/test_ride_insert_and_dispatch_prep.py`
  + `backend/tests/test_ride_preauth_booking.py`
  + `backend/tests/test_create_ride_remaining_branches.py` (booking.py),
  `backend/tests/test_ride_estimate_branches.py` (estimates.py),
  `backend/tests/test_ride_cancellation_branches.py` (cancellation.py).
  Combined with PR #2557 (merged): `matching.py` **64.7% → 79.41%** — the
  only remaining file in the `routes/rides/` package, ~0.6pp short of the
  80% target. Not scheduling a dedicated follow-up for the remainder; see
  Acceptance below.
- **Approach:** measure current per-file coverage (`pytest --cov --cov-report=term-missing`),
  write tests for the uncovered branches (fare tiers, surge, corporate, promo, refund,
  webhook types, ride-state transitions), then enforce with
  `coverage report --fail-under` per path or a `ci-guardrails` step. Ratchet,
  don't big-bang — one file/PR at a time, per CLAUDE.md's ≤3-files-per-subtask
  rule.
- **Acceptance:** CI fails if payments/fare coverage drops below 90% or
  `routes/rides/*` / dispatch below 80%. Payments, fare, dispatch,
  `lost_found.py`, `lifecycle.py`, `receipts.py`, `queries.py`,
  `estimates.py`, `cancellation.py`, and `booking.py` now meet target.
  `matching.py` (64.7% → 79.41% combined, PRs #2557 + #2561) is the only
  file still ~0.6pp short of the 80% target — not worth a dedicated
  follow-up PR; can be closed opportunistically alongside a future change
  to the file.

### A2. Post-deploy smoke test in CI
- [ ] **Status:** open
- **Why:** deploys to Fly/Railway succeed or fail silently; a bad deploy is currently
  discovered by users. The smoke script from PR #172 already exists.
- **Files:** `.github/workflows/deploy-fly.yml`, `.github/workflows/deploy-backend.yml`
- **Approach:** add a job after deploy that curls `/health`, exercises auth (expect 401
  not 500), and the fare-service health path with `--fail-with-body`; page on failure.
- **Acceptance:** a deliberately broken deploy turns the workflow red within minutes.

### A3. PIPEDA breach record register
- [ ] **Status:** open (regulatory)
- **Why:** referenced by `docs/runbooks/data-breach.md` but never created; PIPEDA
  requires a 24-month breach record.
- **Files:** create `docs/audit/breach-record.md`
- **Acceptance:** template with columns (date, scope, RROSH assessment, notified?,
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

### A7. 8 failing tests in `test_ai_tools_booking.py` on `main`
- [ ] **Status:** open — found via PR #2523's `backend-test` run (2026-07-27),
  confirmed reproducible directly on `origin/main` in isolation (checked out
  `backend/tests/test_ai_tools_booking.py` + `backend/ai/` from `main` alone
  and re-ran — identical failures), so this is pre-existing drift already on
  `main`, not introduced by any A1/A6 branch this session.
- **Why:** all 8 failures are `KeyError` — either `'quotes'` or
  `'_client_action'` — across `TestSameStreetGuard`, `TestFareQuote`,
  `TestSamePlaceGuard`, `TestProposal`. Pattern strongly suggests the AI
  booking tool's response shape changed (likely a key renamed or moved to a
  different response envelope) and these tests were never updated — the same
  class of test-drift root cause as A4.
- **Files:** `backend/tests/test_ai_tools_booking.py`, and whichever
  `backend/ai/` module builds the booking-tool response (likely
  `ai/tools_booking.py` or similar — not yet located).
- **Approach:** read the current booking-tool response builder, compare
  against what the 8 tests assert, and either fix the tests to match
  already-correct current behavior or, if the response shape itself is wrong,
  fix the production code — root-cause first, same as every A4 bucket.
- **Acceptance:** `pytest tests/test_ai_tools_booking.py` passes with 0
  failures on `main`.

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
- [ ] **Status:** open — parked pending a pricing decision (2026-07-27).
  **Root cause confirmed against production data** (queried live Supabase):
  this is a **data problem, not a code bug**. All 5 active `service_areas`
  rows have real `vehicle_pricing` JSONB entries — the fare service's
  per-vehicle-type join/lookup logic (`routes/fares.py::build_fares_for_area`)
  is correct and does NOT fall through to `DEFAULT_FARE` for these areas.
  The rows themselves were configured with **identical rate numbers across
  every vehicle type in every area**:
  - `Regina Airport`, `riyadh`, `riyadh airport`: all 4 vehicle types
    (Economy/XL/Van/Premium) carry the exact `DEFAULT_FARE` values
    (base_fare 3.50, per_km 1.50, per_min 0.25, min_fare 8, booking_fee 2) —
    almost certainly seeded by copying the fallback defaults verbatim rather
    than falling back to them at request time.
  - `Regina`, `Saskatoon`: Economy and XL both configured, but with the same
    numbers as each other in each area (Regina: 2/2/0/0/0 both; Saskatoon:
    4/1/0/0/0 both) — looks like a row was duplicated in the admin Vehicle
    Pricing editor and only the `vehicle_type` name field was changed, not
    the rate fields.
  - `riyadh`/`riyadh airport` are intentional (international market),
    not a data-hygiene concern — confirmed with product.
  - **Not a live-testing blocker**: booking end-to-end still works, no
    fare/receipt mismatch, no payment-integrity or safety issue — riders
    just see the same price across vehicle types, which could read as "the
    picker does nothing" if a tester notices.
- **Action (blocked on a pricing decision, not on more investigation)**:
  someone with pricing authority needs to supply real differentiated
  base_fare/per_km/per_min/min_fare/booking_fee values per vehicle type per
  area (or approve applying industry-standard multipliers off each area's
  existing Economy rate — proposed: XL/Van ≈1.4×, Premium ≈1.8× on
  base_fare/per_km, more modest ~1.2×/~1.5× on per_min/booking_fee) — then
  `UPDATE service_areas SET vehicle_pricing = ...` per area. No code change
  needed; the join logic is already correct and tested
  (`backend/tests/test_fares.py`).
- **Files:** none (data-only fix) — reference only:
  `backend/routes/fares.py::build_fares_for_area`,
  `backend/tests/test_fares.py`
- **Acceptance:** each vehicle type in each area quotes genuinely different
  rates reflecting its class (XL/Premium priced above Economy).

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
