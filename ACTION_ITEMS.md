# Spinr — Production Readiness Action Items

> **How to use this file (for Claude / any AI session):** pick the highest-priority
> `[ ]` item, read its *Files* and *Acceptance* fields, implement it following
> `CLAUDE.md` conventions (≤3 files per subtask, one logical change per commit,
> tests required), then flip it to `[x]` with the PR/commit reference in the
> *Done* column. Do not re-litigate `[x]` items. Companion document with full
> context: `docs/PRODUCTION_READINESS.md`.

_Last updated: 2026-07-28 (branch `claude/rider-ai-location-selection-yn0mem` — added B-AI1 + AI1–AI13 from the AI/MCP guardrail audit). Sections: A=launch-gating, B=pre-launch fixes, C=operational, D=post-launch, E=industry-parity._

---

## P0 — Launch gating (code)

### A1. Per-module test-coverage floors for money paths
- [x] **Status:** DONE (2026-07-28) — `matching.py` and `rides/payments.py`,
  the two remaining files below the 80% target, are now closed:
  - `routes/rides/matching.py`: 78% → **89%** (18 new tests,
    `backend/tests/test_rides_matching_coverage.py`), commit `3c83ee8`.
  - `routes/rides/payments.py`: 70% → **96%** (14 new tests,
    `backend/tests/test_rides_payments_coverage.py`), commit `297b776`.
  Test-only, additive changes — no production code touched. All files in the
  `routes/rides/` package and the money-path modules listed below now meet
  or exceed their 80%/90% targets.
  <details><summary>History</summary>

  **Status (2026-07-27):** in progress — measured actual current per-file
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
    `booking.py` 65.7% (now **91.75%** — meets target, PR #2559 + follow-up),
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
    failure fail-open), **now 84.54%** after adding 10 more tests
    (`tests/test_create_ride_remaining_branches.py`) covering `create_ride`'s
    remaining guard clauses: the `service_areas` fetch failure → 503,
    insufficient wallet balance → 400, the pre-dispatch corporate policy
    check (both the 403-with-reasons failure and the passing path's
    `corporate_member_id` resolution), the `work_profile` corporate
    pre-dispatch block (no active membership / policy violation / allowance
    too low to cover the 1.5× buffer — all → 400 with a `reason` code), the
    SCA two-step first-leg early return (hands back `client_secret` without
    ever inserting a ride — the state machine must stay clean until the
    client re-books with the confirmed hold), the DB-level idempotency-key
    replay early return, and `calculate_all_fees` failing mid-booking → 503.
    Meets the 80% target. **Now 91.75%** after a further 7 tests
    (new `tests/test_create_ride_post_insert_branches.py`) covering
    `create_ride`'s post-insert side-effect blocks: promo-code application
    (success — updates `grand_total`/`discount_amount`/`promo_code` on the
    ride; rejection via `HTTPException` — sets `promo_error`, non-fatal;
    unexpected exception — sets a generic `promo_error`, non-fatal), the
    fare-breakdown snapshot save failure (non-fatal, logged), the admin
    live-monitoring broadcast failure (non-fatal), the post-dispatch
    `ride_search_timeout` spawn when the ride is still `searching`, and the
    road-distance settings-fetch exception inside the haversine-fallback
    safety net (swallowed, falls back to the `"road"` mode default). The
    geofence stop-loop's `s_lat is None or s_lng is None: continue` branch
    (line 507) was investigated and confirmed **unreachable via the public
    API** — `CreateRideRequest.validate_stops` already rejects any stop
    missing lat/lng at the Pydantic layer before `create_ride` runs, so it's
    defensive dead code, not a real gap; not covered, and not worth
    covering. Remaining ~8% gap is the corporate work-profile pre-dispatch
    block's happy path and the planned-route-snapshot spawn's inner
    success/failure branches — low-value, not scheduled.
  - `routes/rides/queries.py`: was 69.2%, **now 92.20%** (PR #2544) — meets
    the 80% target.
  - `routes/rides/estimates.py`: was 71.0%, **now 93.99%** (PR #2552) — meets
    the 80% target.
  - `routes/rides/cancellation.py`: was 71.0%, **now 95.06%** (PR #2555) —
    meets the 80% target.
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
  `estimates.py`, `cancellation.py`, `booking.py`, `matching.py` (89%), and
  `rides/payments.py` (96%) all meet target as of 2026-07-28. All A1-scoped
  money/dispatch paths now meet their coverage floors.
  </details>

### A1b. Backend test-coverage floor for the rest of the codebase (scoped, not started)
- [ ] **Status:** open — scoping only, no work started. Raised 2026-07-27 when
  the user asked why A1 only touched money/dispatch paths rather than the
  whole backend. Answer: A1's mandate (CLAUDE.md) is explicitly ≥90% for
  payments/fare and ≥80% for rides/dispatch — not a whole-codebase target.
  The global CI floor (`backend/pytest.ini`) is only 60%, and everything
  outside A1's file list currently sits there or below. This item scopes
  what a deliberate *next* push would look like, split into two tracks so
  a future session can pick either without re-deriving priority order.
- **Why:** the same logic that justified A1 (higher-risk code deserves a
  higher bar) applies to other domains this session never touched —
  corporate billing, safety/SOS, auth/RLS, and admin actions all have
  real-world consequences (money, safety, compliance) if a regression ships
  untested. Full-backend uniform 80% is not the recommendation — see
  Approach below for why targeted beats uniform.
- **Files:** none yet — this is a scoping entry. When picked up, follow the
  same pattern as A1: one file/PR at a time, ≤3 files per subtask, measure
  real `coverage.xml` numbers before writing tests (files are frequently
  already better- or worse-covered than assumed).
- **Approach — Track 1 (money/safety/compliance-adjacent, recommend first):**
  measure current coverage for, in priority order:
  1. Corporate billing layer — **measured 2026-07-28** (post PRs #2615,
     #2696): module aggregate ~52% against a proposed 80% target (see
     `CLAUDE.md`'s coverage-minimums table and `.claude/context/domain-corporate.md`).
     New code from the lifecycle-audit fixes is well-covered (79–90%); the
     gap is concentrated in pre-existing files, priority order for a future
     session:
     - `routes/corporate_accounts.py` — **done, 82%** (was 39% as measured
       against a narrow corporate-only test subset in the original scoping
       pass; re-measured against the full corporate-admin-route test set —
       `test_admin_business_logic.py`, `test_admin_rbac.py`,
       `test_corporate_admin_routes.py`, `test_corporate_b2b_schema.py`,
       `test_corporate_db_helpers.py`, `test_corporate_e2e_foundation.py`,
       `test_corporate_e2e_wallet.py`, `test_corporate_kyb.py`,
       `test_corporate_status.py`, `test_corporate_stripe_customer.py`,
       `test_corporate_wallet_bootstrap.py`, `test_corporate_wallet_freeze.py`,
       `test_db.py`, `test_deprecated_route_admin_exempt.py`,
       `test_error_response_sanitisation.py`, `test_features.py`,
       `test_p3_admin_jwt_modules.py`, `test_stripe_event_loop_offload.py`
       — the real baseline was 77%, not 39%. +9 tests
       (`test_corporate_admin_routes.py`) closed the highest-value remaining
       gaps: validator no-ops, the `is_active` list filter, the
       `X-Total-Count` exception fallback, the previously entirely-untested
       `kyb_upload_url` endpoint, and two `kyb_document_confirm` error
       branches. Meets the 80% target. Remaining 18% is `change_company_status`'s
       deepest nested exception branches and `kyb_review`'s email-failure
       paths — lower priority, not pursued further in this pass. See
       `docs/change-log/2026-07-28-corporate-accounts-coverage-80.md`.
     - `services/corporate_wallet_service.py` — **done, 97%** (closed
       2026-07-28, see `docs/change-log/2026-07-28-corporate-wallet-service-coverage-80.md`),
       `services/corporate_allowance_service.py` — **done, 97%**
       (closed 2026-07-28 — the existing `tests/services/test_corporate_allowance_service.py`
       already covered every branch once measured in isolation (`pytest tests/ -k allowance`);
       the previously-tracked 39% figure was stale/measured differently, no new tests were
       needed; see `docs/change-log/2026-07-28-corporate-allowance-service-coverage-80.md`)
     - `routes/corporate_company_bookings.py` — was 38%, now **87%** (test-only
       PR `claude/corporate-company-bookings-coverage`, 2026-07-28): added
       `backend/tests/test_corporate_company_bookings_coverage.py` (31 tests
       incl. the pre-existing `test_corporate_sections.py`) covering
       `create_booking`, `list_bookings` (tenancy scoping, filters, N+1-free
       batch joins), `cancel_booking` (authz + tenancy + missing-guest paths),
       `booking_fare_estimate`, `_require_company_active`, and the
       `_booking_row` OTP-redaction projection. Remaining 13% is dual-import
       `except ImportError` fallback lines (structurally only one branch runs
       per process) and section-CRUD duplicate/404 paths already exercised by
       `test_corporate_sections.py` — diminishing returns, see
       `docs/change-log/2026-07-28-corporate-company-bookings-coverage-80.md`.
     - `routes/corporate_signup.py` — **89%** (was 32-33%; closed
       2026-07-28, see `docs/change-log/2026-07-28-corporate-signup-coverage-80.md`).
       Remaining 7 uncovered lines are the dual-import `ImportError` fallback
       block (untestable per the dual-import convention, not a real gap).
     - `routes/corporate_rider.py` — **97%** (closed 2026-07-28, up from
       32-33%, see `docs/change-log/2026-07-28-corporate-rider-coverage-80.md`).
     - `routes/corporate_company_kyb.py` — **closed 2026-07-28: 32-33% →
       98%** (target was 80%). See
       `docs/change-log/2026-07-28-corporate-company-kyb-coverage-80.md`.
     - `services/corporate_membership_service.py` — **100%** (was 27%,
       closed 2026-07-28: 17 unit tests added for the invite/accept race
       branches, domain auto-match edge cases, `join_via_domain` fallback,
       `_uuid_or_none` actor-id coercion, and both `bootstrap_owner` modes
       — test-only change, see `docs/change-log/2026-07-28-corporate-membership-service-coverage-80.md`),
       `services/corporate_policy_service.py` — **98%** (was 68%, closed
       2026-07-28: 13 unit tests added for `evaluate_policy_for_ride` —
       previously entirely untested (0 of its 52 lines) — covering the
       happy path, no-matching-membership, policy-fetch-failure fail-open,
       membership-lookup-failure graceful-degradation, and member-level vs
       caller-level `policy_override` precedence; plus 2 pure-function
       branch tests for datetime-object and tz-aware `pickup_time` in
       `evaluate_policy`'s time-window rule — test-only change, see
       `docs/change-log/2026-07-28-corporate-policy-service-coverage-80.md`)
     All items in this track are now at or above the 80% target.
  2. `backend/utils/insurance_periods.py`, safety check-in / SOS-related
     routes (see `.claude/context/domain-safety.md`) — regulatory +
     rider/driver safety consequence if untested code has a latent bug.
     Closed 2026-07-29:
     - `backend/utils/insurance_periods.py` — see
       `docs/change-log/2026-07-29-a1b-insurance-periods-coverage.md`.
     - `backend/routes/safety.py` — see
       `docs/change-log/2026-07-29-a1b-routes-safety-coverage.md`.
     - `backend/routes/rides/safety.py` — see
       `docs/change-log/2026-07-29-a1b-rides-safety-coverage.md`.
     - `backend/routes/admin/safety.py` — see
       `docs/change-log/2026-07-29-a1b-admin-safety-coverage.md`.
     - `backend/utils/safety_checkin_loop.py` — coverage raised 85%→87%,
       see `docs/change-log/2026-07-29-a1b-safety-checkin-loop-coverage.md`.
       Writing the coverage tests surfaced a **real production bug**, since
       fixed: the module's `except ImportError` fallback (the branch
       actually active in production) never imported `notify_safety_team`,
       so every auto-escalated no-response safety check-in silently
       NameError'd inside `_escalate`'s broad exception handler — the
       incident row and audit log were written, but the safety team was
       never actually paged (no WS broadcast, no email, no PagerDuty log
       line). Fixed by adding `notify_safety_team` to the fallback import
       list to mirror the `try` branch, plus a regression test
       (`test_escalate_calls_notify_safety_team`) that fails against the
       pre-fix code and passes now. See
       `docs/change-log/2026-07-29-safety-checkin-notify-fix.md`.
  3. Auth/RLS-adjacent code: JWT handling, OTP verification
     (`backend/utils/crypto.py` is already tracked at ≥90% target per
     CLAUDE.md but should be re-verified), refresh-token rotation
     (`backend/utils/refresh_tokens.py`). **In progress (2026-07-29):**
     - `backend/utils/crypto.py` — re-verified: **100%**, already exceeds
       the ≥90% target, no work needed.
     - `backend/utils/refresh_tokens.py` — **done, 99%** (was 62%; the
       existing test file only pinned the reuse-detection cascade —
       `issue_refresh_token`, `lookup_refresh_token`'s remaining branches
       (empty input, DB error, expiry parsing), `revoke_refresh_token`,
       and `revoke_all_for_user` had zero coverage). Added 25 new tests
       across two files: `tests/test_refresh_tokens_lifecycle.py` (mint/
       lookup/revoke lifecycle) and one Sentry-capture-failure test added
       to the existing `tests/test_refresh_token_reuse_detection.py`.
       Remaining 1 line is the dual-import fallback. Test-only, no bugs
       found. See `docs/change-log/2026-07-29-a1b-refresh-tokens-coverage.md`.
     - `repositories/auth_repo.py` — **done, 96%** (was 67%; had zero
       dedicated test file). Added `tests/test_auth_repo.py` (18 tests)
       covering the "Supabase client not configured" branch and the happy
       path for all 8 functions (user lookup/creation, OTP CRUD).
       Remaining 2 lines are the dual-import fallback. Test-only, no bugs
       found. See `docs/change-log/2026-07-29-a1b-refresh-tokens-coverage.md`.
     - `dependencies/__init__.py` — **done, 93%** (was 62%/77%; the JWT
       auth-gate module's Firebase-token success path — uid lookup, phone
       fallback, session-revocation via `sessions_invalid_before`, driver
       caching, deleted-account enforcement — had **zero** direct
       coverage; existing tests only exercised the "not a Firebase token,
       fall through to JWT" branch. Also closed `_verify_admin_payload`'s
       staff-inactive / stale-token-version / idle-timeout / malformed-
       timestamp branches, JWT-path DB-error propagation (never silently
       swallowed, per CLAUDE.md), and `get_current_user_allow_expired`'s
       admin-audience-gets-no-grace and not-actually-expired branches.
       Added `tests/test_dependencies_auth_gaps.py` (20 tests). Remaining
       21 lines are the dual-import fallback plus a handful of
       log-statement-only branches. Test-only, no bugs found.
     - `core/middleware.py` — **done, 81%** (was 60%/69%;
       `_validate_production_config` — the fail-fast guard that stops a
       misconfigured deploy from ever serving traffic when
       `ENV=production` — was only ever *patched away* (mocked out) in
       `test_p1_cors.py`, never exercised directly. Added
       `tests/test_middleware_production_config_guard.py` (16 tests)
       covering every one of its 5 checks (JWT secret weak/short,
       Supabase URL missing/placeholder, service-role key
       missing/malformed/short, admin creds weak, rate-limit Redis URL
       missing/non-redis-scheme) both individually and combined into one
       `RuntimeError`, plus the Firebase-creds-missing warn-only path.
       Remaining gap is four nested middleware classes defined inside
       `init_middleware(app)` (App Check enforcement, CORS exception
       handler, relative-redirect rewriting, deadline propagation) —
       these need `TestClient`-level request testing, not unit-testable
       in isolation; lower priority, diminishing returns for this pass.
       See `docs/change-log/2026-07-29-a1b-dependencies-middleware-coverage.md`.
     - `routes/auth.py` — **55%→69%** (`verify_otp` — the core rider/driver
       login/signup endpoint, 382 lines — had zero direct coverage of its
       success path; existing tests only pinned the lockout helpers and
       the "DB error is not a wrong code" 503 case). Added 13 tests
       (`tests/test_verify_otp_login_flow.py`) covering existing-user
       login, guest-account claim-on-verify (`is_guest` cleared),
       session-update-failure-doesn't-block-login, the PIPEDA
       `pending_deletion` reactivation handoff, the fully-deleted-account
       410, new-user creation (+ `create_user` DB-failure → 503, never
       mints a token for an unpersisted row), and 4 OTP-record validation
       branches (wrong code, expired, malformed/missing `expires_at`).
       Still open in this file: the company-email-OTP flow
       (`send_company_email_otp`/`verify_company_email_otp`),
       `firebase_auth_login`, `refresh_access_token`,
       `logout`/`logout_all`, and `reactivate_account` — none of these
       endpoints have direct tests yet; large remaining scope, not
       pursued further in this pass. See
       `docs/change-log/2026-07-29-a1b-verify-otp-coverage.md`.
     - `routes/admin/auth.py` — **done, 94%** (was 70%, re-measured fresh
       against the full suite — the previously-tracked 64-70% figure was in
       the right ballpark). The endpoint was well-covered for login/MFA/
       refresh/logout flows by 9 pre-existing sibling test files
       (`test_admin_mfa_enforcement.py`, `test_admin_mfa_totp_lockout.py`,
       `test_admin_login_resets_idle_clock.py`, `test_admin_logout_revocation.py`,
       `test_admin_token_aud_lockdown.py`, `test_admin_privilege_escalation.py`,
       `test_admin_staff_mfa_reset.py`, `test_admin_security.py`,
       `test_admin_routes_auth.py`) — the entire gap was two endpoints with
       **zero** direct coverage: `/admin/auth/break-glass` (emergency
       super-admin token mint — every guard branch: feature-gated-off,
       short justification, Redis-unreadable/increment-failure/allowlist-
       failure fail-closed paths, rate-limit-exceeded, invalid token, and
       the happy path incl. audit-log-write-failure being logged but not
       blocking) and `/admin/auth/unlock` (role guard, empty email,
       target-not-found, idempotent not-locked path, Redis-read failure,
       successful unlock) — plus `/mfa/status`, `/mfa/enroll`, `/session`'s
       malformed-header shapes, `/refresh`'s admin-001 branch, and
       `/logout-all`'s malformed-token branches. Added
       `tests/test_admin_auth_coverage_gap.py` (33 tests). Remaining 6% is
       the dual-import fallback plus a few log-only branches in
       `_require_staff_from_token`. Test-only, no bugs found. See
       `docs/change-log/2026-07-29-a1b-admin-auth-coverage.md`.
  4. `backend/routes/admin/` (15+ admin-only endpoints) — admin actions are
     audited but not necessarily tested; a broken admin endpoint can corrupt
     production data at scale (e.g. bulk driver approval, wallet
     adjustments).
     - `backend/routes/admin/drivers.py` — **improved 59% → 70%** (1015
       statements, 301 remaining uncovered; measured via full `pytest tests/
       -q`, real pytest-cov output). Prioritized write/mutation endpoints
       over read-only list/search per the item's stated risk (a broken
       write here can lock a real driver out or leave an ineligible driver
       online — regulatory consequence under the Saskatchewan
       Transportation Act driver-eligibility rules). Added
       `tests/test_admin_drivers_coverage.py` (52 tests) covering:
       `POST /drivers/{id}/action` (approve/suspend/ban/unban/reactivate,
       missing-reason 400s, driver-not-found 404, DB-failure 500,
       push-failure non-fatal), `PUT /drivers/{id}/status-override`,
       `POST /drivers/{id}/verify`, `PUT /drivers/{id}` (field routing
       across `users`/`drivers`, null-coalescing, work-authorization-status
       flag sync, 409 on email/gender without a linked user), driver notes
       CRUD, `POST/GET /drivers/{id}/photo` + `/photo-review`,
       `PUT /drivers/{id}/area`, `POST /drivers/{id}/nudge-expiry`,
       `POST /drivers/{id}/refresh-stripe-kyc`, and
       `POST /drivers/{id}/reveal-sin` (super_admin-only gate, SIN never
       logged, Stripe-failure 502). Deprioritized (left at their existing
       coverage): the pure read/list/export endpoints
       (`GET /drivers`, `/drivers/stats`, `/drivers/approval-queue`,
       `/drivers/expiring`, referral leaderboards/analytics,
       payouts-summary, location-trail, daily-activity) — lower real-world
       consequence than a broken write, and several are already exercised
       by `test_admin_approval_queue.py` / `test_admin_drivers_expiring.py`
       / `test_referral_analytics.py`. **Two pre-existing bugs found, not
       fixed (test-only task)** — see
       `docs/change-log/2026-07-29-a1b-admin-drivers-coverage.md` for
       detail: (1) `admin_driver_action`'s `DriverActionRequest.action`
       Literal and docstring both list `"reject"` as valid, but the
       if/elif chain has no `reject` branch — it 400s with "Unknown
       action: reject", so an admin can never reject a driver application
       through this endpoint; (2) `admin_override_driver_status`'s
       `DriverStatusOverride.status` Literal includes `"rejected"` but the
       endpoint's own `valid` set does not (and vice versa for
       `"needs_review"`), so some pydantic-valid status values 400 at the
       handler's internal guard.
- **Approach — Track 2 (breadth, lower urgency):** everything else currently
  below the 60% CI floor or in the 60-80% band with no explicit target —
  utils/services not touched by Track 1. Lower priority; only worth
  picking up once Track 1 is done or if a specific file becomes a live
  incident source.
- **Explicitly NOT recommended:** raising the CI floor to 80% uniformly
  across the whole backend in one move. Many low-risk files (CSV export
  helpers, LMS integration, one-off admin scripts) would cost
  disproportionate effort for coverage that doesn't reduce real risk —
  same diminishing-returns logic that stopped A1's `matching.py` pass at
  79.4% rather than chasing the last 0.6%.
- **Also explicitly out of scope for this item:** frontend test coverage
  (rider-app/driver-app/admin-dashboard — React Native / Next.js, not
  measured or covered by anything in A1/A1b) and a correctness audit of
  fare/pricing *values* (e.g. whether Economy vs. XL vehicle-type pricing
  in `fare_configs` is intentional — that data lives in the live DB via the
  admin dashboard's Service Areas → Vehicle Pricing editor, not in this
  repo, and needs a live DB read to answer, not a coverage pass). Both are
  real, separate asks the user raised in the same session as A1b's
  scoping — track them as their own items if/when the user wants them
  picked up, don't fold them into A1b.
- **Acceptance:** not yet defined — pick a track and file list with the
  user before starting; don't assume "cover everything to 80%" is the
  goal without confirming, per the "explicitly NOT recommended" note above.

### A2. Post-deploy smoke test in CI
- [x] **Status:** done — already implemented before this checklist was last
  reviewed. `.github/workflows/ci.yml`'s `smoke-test` job curls `/health` +
  DB check, `app_settings`, `vehicle-types`, and confirms auth/fare-estimate
  return 401 not 500; `notify-failure` job alerts on failure. `deploy-fly.yml`/
  `deploy-backend.yml` already have health-poll-and-rollback logic. Landed in
  commit `3bae3db`. (Note: a prior attempt to mark this done, PR #2504,
  reported `merged: true` on GitHub but its commit never actually landed on
  `main` — re-applying the doc fix here.)
- **Why:** deploys to Fly/Railway succeed or fail silently; a bad deploy is currently
  discovered by users. The smoke script from PR #172 already exists.
- **Files:** `.github/workflows/deploy-fly.yml`, `.github/workflows/deploy-backend.yml`
- **Approach:** add a job after deploy that curls `/health`, exercises auth (expect 401
  not 500), and the fare-service health path with `--fail-with-body`; page on failure.
- **Acceptance:** a deliberately broken deploy turns the workflow red within minutes.

### A3. PIPEDA breach record register
- [x] **Status:** done — already implemented before this checklist was last
  reviewed. `docs/audit/breach-record.md` exists with a superset of the
  requested columns (date, scope, RROSH assessment, notified?, evidence
  location) and the required "no entries to date" first row. Created in PR
  #2222 (2026-07-25). (Note: a prior attempt to mark this done, PR #2504,
  reported `merged: true` on GitHub but its commit never actually landed on
  `main` — re-applying the doc fix here.)
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
- [x] **Status:** done (2026-07-28) — actually 9 failing tests, not 8 (the
  count in this item's own text was stale). Root-caused to two distinct
  test-drift causes, both against genuinely-newer production code, not a
  production bug:
  1. **8 of 9** — a `_dropoff_pair_refusal` guard (shipped for the Walmart
     dropoff-label incident, correctly fails closed when Maps/Places is
     unavailable) now runs inside both `get_fare_quote` and
     `propose_ride_booking` and intercepts *before* the guard each test
     actually means to exercise (same-street, same-place, fare-quote,
     proposal). These tests mock Maps as unavailable to isolate an unrelated
     guard, which used to be safe — the new guard changed that. Fixed by
     bypassing it (`patch.object(tools_booking, "_dropoff_pair_refusal",
     AsyncMock(return_value=None))`), matching the pattern already
     established by sibling tests in the same file that predate this guard's
     addition.
  2. **1 of 9** (`test_proposal_reresolves_pickup_address_when_coords_are_stale`)
     — `_resolve_candidate_areas` (a batched service-area lookup added later
     as an optimization — "one read, not an N+1 loop" — for tagging geocode
     candidates) bypasses the older `_resolve_area` mock this one test still
     relied on exclusively. Fixed by also mocking
     `_resolve_candidate_areas`.
- **Files:** `backend/tests/test_ai_tools_booking.py` only — no production
  code changed; both guards are correct, newer behavior.
- **Acceptance:** ✅ met — `pytest tests/test_ai_tools_booking.py` reports
  72 passed, 0 failed. Full local backend suite re-run to confirm no
  regressions elsewhere.

### A8. Leaked un-awaited AsyncMock coroutines fail an arbitrary unrelated test under pytest 9
- [ ] **Status:** open — diagnosed 2026-07-29 while verifying the admin driver
  search fix (`claude/admin-driver-search-design-ryh7yc`). Not fixed there: the
  leaks are in test files that change did not touch, and closing them is its own
  scoped cleanup.
- **Symptom:** a full-suite run fails one test that passes in isolation, and
  *which* test fails changes between runs at the same commit. Observed:
  `test_compliance_reports_http.py::test_knight_archer_report_filters_by_status`
  failed one full-suite run and passed the next at an identical commit, while
  passing 5/5 in isolation.
- **Root cause:** several test files leave `AsyncMock` coroutines un-awaited
  (`RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never
  awaited`). The warning surfaces whenever the GC happens to collect the
  coroutine — not where it was created. pytest 9's `_pytest/unraisableexception`
  plugin **re-raises** collected unraisable errors (`raise errors[0]`), so the
  test executing at that moment fails. Blame is therefore assigned by GC timing,
  and *any* change to test count or ordering reshuffles the victim. Confirmed
  present at baseline: `tests/test_ride_accept_flow.py` +
  `tests/test_drivers_extended.py` alone emit 7 such warnings on an unmodified
  checkout.
- **Why it matters:** this makes the suite an unreliable merge gate — a green
  run does not mean the leaks are gone, and a red run points at an innocent
  test. It also burns review time re-diagnosing the same thing each session
  (this entry exists so the next session doesn't).
- **Fix (proposed):** find every `AsyncMock` whose call result is never awaited
  (`grep` for `AsyncMock(` used as a sync `side_effect`/`return_value` on a
  method the code under test calls synchronously — e.g. `supabase.rpc(...)`
  chains inside `run_sync` lambdas) and make the mock synchronous
  (`MagicMock`) where the production call site is synchronous. Add
  `-W error::RuntimeWarning` for the leak class once clean so it cannot
  regress silently.
- **Files (known leak sources, non-exhaustive):**
  `backend/tests/test_ride_accept_flow.py`, `backend/tests/test_drivers_extended.py`,
  `backend/tests/test_e2e_ride_lifecycle.py`, `backend/tests/test_estimate_ghost_driver_filter.py`
- **Acceptance:** full backend suite produces zero
  "coroutine ... was never awaited" warnings, and 3 consecutive full-suite runs
  at the same commit fail no tests.

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
- [x] **Status:** done (2026-07-28) — investigated all 3 sub-issues. All 3
  turned out to already be fixed by prior migrations before this session
  started; only a narrow defense-in-depth gap remained. **Correction partway
  through**: an initial draft of the RLS fix assumed migration 10's
  `FOR ALL TO authenticated` policy was still live and tried to replace it —
  a `spinr-migration-reviewer` subagent review caught that this was stale
  (migration 142 already superseded it) before merge; see the migration's
  own header for the full correction narrative.
  1. **RLS too broad — already fixed by migration 142, months before this
     session.** `142_fix_rls_financial_tables.sql` dropped migration 10's
     `FOR ALL TO authenticated` policy and replaced it with SELECT-only
     policies (`"Admin read disputes"` role-checked, `"Rider read own
     disputes"` own-row), revoking all INSERT/UPDATE/DELETE/TRUNCATE grants
     from `authenticated`. The one gap it left: `service_role` still
     bypasses RLS by design (correct — backend needs INSERT/UPDATE) and
     nothing blocked a `service_role` DELETE. `backend/migrations/262_disputes_rls_lockdown.sql`
     closes exactly that gap with a `BEFORE DELETE` trigger blocking
     deletion for every role including `service_role` (pattern: `audit_logs`,
     migration 51) — it does not touch 142's RLS policies/grants at all.
     Confirmed via grep that no live code path deletes disputes — the one
     `delete_many("disputes", ...)` call (`routes/admin/support.py`) is dead
     code, never imported/mounted by `server.py` or `features.py`.
  2. **Refund-cent rounding — already fixed, no action needed.**
     `admin_resolve_dispute` (`routes/disputes.py:219`) uses
     `dollars_to_cents()`, which does proper Decimal HALF_UP conversion, not
     bare `int()` truncation. Covered by `backend/tests/test_dispute_refund_cents.py`.
     The backlog text describing this as still-broken was stale.
  3. **Full legal name in admin response — already fixed by migration 142,
     months before this session.** Migration 142 §3 already scrubbed the
     `disputes.user_name` column (`UPDATE disputes SET user_name = ''`) and
     its own comment states the backend "no longer writes this column" —
     the admin list endpoint enriches the display name at read time from
     `users` instead (PIPEDA data minimization, already done). My first pass
     at this investigation only grepped current application code and missed
     migration 142 entirely, incorrectly concluding the column was merely
     "dead but harmless" and that no fix was needed — the fix had already
     shipped. Corrected once the migration reviewer's findings surfaced the
     full picture.
- **Files:** `backend/migrations/262_disputes_rls_lockdown.sql` (new, DELETE-block
  trigger only — see file header for the full correction from its first draft)
- **Acceptance:** ✅ met — refund cents already use proper rounding; disputes
  RLS was already locked to SELECT-only by migration 142; the full-name PII
  scrub was already done by migration 142; DELETE on disputes is now blocked
  at the DB level for every role including `service_role`.

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
- [x] **Status:** done (2026-07-28) — `ConnectionManager.note_user_message`
  now enforces the 30 msg/s cap via a Redis fixed-window counter (`INCR`
  then `EXPIRE 1` on the first increment), keyed on `user_id` and shared
  fleet-wide. `utils/redis_client.py` already transparently falls back to
  an in-process dict when `REDIS_URL` is unset, so local/dev/test needed
  no branching. If Redis IS configured but a call raises (network blip,
  Redis down), the limiter fails **open** to the original per-machine
  sliding-window bucket (renamed `_note_user_message_local`) rather than
  blocking every WS message fleet-wide on a transient Redis hiccup —
  matching the non-security-critical fail-open precedent already in
  `utils/rate_limiter.py`'s `RedisRateLimiter` (OTP keys fail closed;
  general limits degrade to memory).
- **Files:** `backend/socket_manager.py`, `backend/routes/websocket.py`
  (awaited the now-async call), `docs/runbooks/websockets.md`,
  `backend/tests/test_websocket_per_user_rate_limit.py`,
  `backend/tests/test_websocket_auth.py`.
- **Acceptance:** ✅ met — cap holds at N msg/s per user across all
  replicas via the shared Redis counter; verified with the existing
  30/31-message contract tests plus new coverage for the Redis-failure
  fallback path and its bucket cleanup. Full local backend suite re-run
  to confirm no regressions elsewhere.

### B5. Migrate AI place lookup to Places API (New) with hard locationRestriction
- [x] **Status:** done (2026-07-28) — the named-place ("places") branch of
  `_lookup_place_candidates` in `backend/ai/tools_booking.py` now calls
  Places API (New) Text Search (`places:searchText`) instead of the legacy
  Text Search API. Text Search (New)'s `locationRestriction` only accepts a
  **rectangle** (not a circle, unlike Autocomplete New) — that rectangle IS
  a hard filter: Google cannot return a candidate outside it at all, unlike
  the legacy API's soft `bounds`/`radius` params. A soft `locationBias`
  circle (matching the rider-app's Autocomplete-New pattern) rides alongside
  it purely as a relevance-ranking nudge. New helpers
  `build_text_search_payload` / `legacy_place_results_from_text_search` live
  in `backend/utils/google_places_new.py`, alongside the existing
  Autocomplete/Details helpers, and translate to/from the same legacy
  candidate shape the rest of `tools_booking.py` already expects — no
  downstream code (`_candidates_from_results`, dedup, precision flagging)
  needed to change.
  - **Also fixed, found while implementing**: `record_call("places_text_search")`
    was a string outside `maps_budget.py`'s `Sku` Literal and `_PRICE_USD`
    dict — every such call silently miscounted against a Redis key
    `estimate_today_usd()` never reads, so the circuit breaker was blind to
    this entire call type. Added the real `text_search_new` SKU (Places API
    (New) Text Search Pro pricing) so it now counts toward the daily budget.
  - **Verified NOT a gap** (the backlog text's other budget claim was
    stale): the fare Directions calls already call
    `record_call("directions")` (`utils/route_distance.py:734`), and
    `"directions"` was already a priced SKU — no fix needed there.
  - **Not in scope, intentionally**: the geocode branch (street addresses)
    stays on the legacy Geocoding API — Places API (New) has no forward-
    geocoding surface to migrate it to. The hard-filter fix for THAT branch
    is `components=locality:<city>`, already tracked separately as B7
    (blocked on `service_areas` gaining a real locality column).
  - **Deferred, not done here** (explicitly out of this item's title/scope):
    a hard `estimate_token` price-lock across the chat→card gap, a blocking
    surge sheet on the AI confirm card, and a structured (non-prose) payload
    for quote-card taps — these remain open follow-ups, not touched.
- **Files:** `backend/utils/google_places_new.py` (new payload
  builder/translator), `backend/ai/tools_booking.py` (new `_maps_post`
  helper + "places" branch rewrite), `backend/utils/maps_budget.py` (new
  `text_search_new` SKU), `backend/tests/test_ai_tools_booking.py` (rewrote
  `PLACES_OK` fixture + affected tests to the New API response shape; added
  `TestFindPlaceHardRestriction` pinning the hard rectangle, the no-bias-point
  case, and error-status handling), `backend/tests/test_maps_proxy.py`
  (pinned `text_search_new` counts toward `estimate_today_usd`).
- **Acceptance:** ✅ met — AI place lookups (named-place branch) never return
  a candidate outside the bias rectangle; `estimate_today_usd` now counts
  Text Search (New) calls (previously invisible) and already counted
  Directions calls (verified, not a real gap).

### B6. Measure Directions latency and re-tune the fare-estimate wait
- [ ] **Status:** in progress (2026-07-28) — the measurement half is done;
  the re-tuning half is genuinely blocked on live traffic this dev session
  cannot produce, not on more code work.
  - **Done:** `estimates.py`'s `_route_fetch()` now times every real
    Directions call and records it to `spinr_fare_directions_duration_ms`
    (new histogram, `utils/metrics.py`'s existing `observe`/`_metric_observe`
    plumbing — no new metrics infrastructure needed). Recorded in a `finally`
    block so a slow **or failed** call still shows up — a request that hits
    `DIRECTIONS_TIMEOUT_S` and gets cut off is exactly the signal this metric
    exists to surface, and a silently-dropped failure would hide the worst
    tail instead of measuring it. This follows the exact convention
    `utils/metrics.py`'s own `time_ms()` context manager documents ("Records
    even when the block raises — a slow failure is still latency the SLA
    dashboards must see").
  - **Not done, and can't be from this session**: picking the timeout from
    the observed p99. That requires real production request volume against
    the live Google Directions API — this dev session has neither live
    traffic nor Maps API access to generate a genuine distribution; a
    synthetic/mocked one would defeat the entire point of B6 (replacing
    judgement with data). `DIRECTIONS_TIMEOUT_S` / `_PRICING_ROUTE_WAIT_S`
    are therefore **unchanged** — still 1.5 s / 2.0 s, still by judgement,
    now with the instrumentation in place to replace that judgement once
    `spinr_fare_directions_duration_ms` has accumulated real traffic.
    `test_pricing_wait_stays_within_the_estimate_latency_budget` needed no
    change since the ceiling itself didn't move.
  - **Next step for whoever picks this back up**: let the metric collect for
    a representative window in production, pull the p99 from
    `/metrics` (or wherever it's scraped to), then decide per the original
    Action text — tighten both constants if the p99 sits well under 1.5 s,
    or move to pre-warming/caching common origin-destination pairs if
    Directions is routinely slower than the SLA allows.
- **Files:** `backend/routes/rides/estimates.py` (instrumentation),
  `backend/tests/test_ride_estimate_branches.py` (2 new tests: metric
  recorded on success, metric recorded even when the Directions call fails).
  `backend/routes/rides/_shared.py` / `backend/utils/metrics.py` needed no
  changes — the histogram plumbing already existed and `_shared.py`'s
  `DIRECTIONS_TIMEOUT_S` wasn't touched (no data to justify moving it yet).
- **Acceptance:** partially met — the latency distribution is now being
  recorded (the prerequisite the original acceptance text assumed already
  existed); the timeout itself is not yet re-justified by real data, and
  can't be inside a single dev session. Re-open once
  `spinr_fare_directions_duration_ms` has real production data to act on.

### B7. Give service areas a real locality so the geocode can be hard-filtered
- [x] **Status:** shipped — PR #2670 (merged 2026-07-28)
- **Why:** the Geocoding API treats `bounds` as a *soft* hint but `components`
  as a **hard** filter. `components=locality:Regina` would make it impossible
  for a Regina query to resolve to a same-named street in another city — the
  strongest available fix for cross-city mis-resolution. It is not wired up
  because `service_areas` has no city column, only `name`, which is a display
  label ("Regina Metro"); a wrong locality returns `ZERO_RESULTS` and breaks
  lookups outright, so a filter built on it is worse than none.
- **Action taken:** reused the existing (previously unpopulated) `city` column
  on `service_areas` — `routes/admin/service_areas.py` already read/wrote it —
  rather than adding a redundant `locality` column. Migration
  `263_service_areas_city_backfill.sql` adds the column defensively and
  backfills 5 known areas by name. `_lookup_place_candidates` in
  `backend/ai/tools_booking.py` now resolves the rider's service area and
  passes `components=locality:<city>|country:CA` when known, via the new
  `_geocode_with_locality_retry` helper, retrying unfiltered once on
  `ZERO_RESULTS`.
- **Files:** `backend/migrations/263_service_areas_city_backfill.sql`,
  `backend/ai/tools_booking.py`, `backend/tests/test_ai_tools_booking.py`
- **Acceptance:** met — a numbered street address in a covered city can no
  longer resolve to another city, and an unknown/unmatched locality degrades
  to prior unfiltered behaviour rather than to zero results.
- **Verified in production (2026-07-28):** `GET /api/v1/service-areas`
  confirms `city` is correctly populated for the real markets — `Regina` →
  `"Regina"`, `Saskatoon` → `"Saskatoon"`. The `riyadh` row (test/dev data,
  not a real market) still shows `city: ""` — migration 263 has not yet been
  applied against production (`schema_migrations` not updated; attempted via
  `scripts/migrate.py` but blocked by IPv6-only direct-host DNS resolution —
  see the `PG_CONNECTION_STRING` / Session pooler note in `CLAUDE.md`'s
  Commands section). Non-blocking: an empty `city` only causes that one row
  to fall back to the pre-PR unfiltered geocode behaviour, no regression.
  Low-priority follow-up: re-run `backend/scripts/migrate.py` against
  production via the Session pooler connection string once convenient.

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
- [x] **Status:** partially done — geocode-verify + dedupe fix shipped; `place_id`
  storage and `CreateRideRequest` cross-field validation explicitly deferred
  (see below)
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
- **Done this pass:** added `backend/utils/address_verification.py` —
  best-effort geocode check used by both `POST /addresses` and
  `POST /favorites` (which `save_favorite_from_ride` delegates into, so both
  entry points from the Why section are covered). Fails OPEN on no API key,
  exhausted Maps budget, API/network error, `ZERO_RESULTS`, `partial_match`,
  or an imprecise (non-ROOFTOP/RANGE_INTERPOLATED) geocode — only rejects
  (400) on a confident precise-geocode mismatch > 1 km. Also fixed the
  favorites dedupe (`favorites.py`) to compare both lat AND lng of both
  pickup and dropoff, not latitude only.
- **Explicitly deferred:**
  - `place_id` storage + re-resolve-on-save — needs a new
    `saved_addresses.place_id` migration column; the write-time verification
    above is the actual safety net, so this is an enhancement on top, not
    required to close the core gap. Left for a follow-up.
  - `CreateRideRequest` cross-field validation in `schemas.py` — ride
    creation is a live, state-machine-critical, money-adjacent surface;
    changing what it accepts needs its own dedicated pass (dry run against
    `mock_supabase_client`, feature-flag consideration) rather than bundling
    into this PR per CLAUDE.md's pre-merge release gates.
- **Files:** `backend/routes/addresses.py`, `backend/routes/favorites.py`,
  `backend/utils/address_verification.py` (new),
  `backend/tests/test_address_verification.py` (new),
  `backend/tests/test_p3_addresses_favorites_safety_disputes.py`
- **Acceptance:** no endpoint persists an address whose stored coordinate is
  more than ~1 km from where that address geocodes, when Google is confident
  about the geocode (met for `/addresses` and `/favorites`; `CreateRideRequest`
  still open — see deferred above).

### B10. Compliance-module exports have no dual-approval gate (extends open AI-3)
- [x] **Status:** DONE (2026-07-29) — shipped across PR #2819 (schema,
  `services/admin_export_approvals.py`, `routes/admin/export_approvals.py`
  queue endpoints, Compliance + Data Transfer server-side gate wiring,
  backend tests) and PR #2820 (admin-dashboard Export Approvals queue page,
  sidebar entry, `202 approval_required` handling in `ExportTab.tsx` and
  `compliance/page.tsx`'s download/email flows). AI-3's shared dual-approval
  mechanism now exists: `settings.dual_approval_exports_enabled` (default
  `false`, dark-launched), a `1,000`-row threshold
  (`_APPROVAL_GATE_ROW_THRESHOLD`), self-approval blocked server-side
  (`require_super_admin` + a distinct-approver check), and both the
  Compliance (`gst-pst-remittance`, `insurance-period-audit`) and Data
  Transfer export endpoints wired through it.
- **Why:** `docs/threat-model/admin-panel.md`'s AI-3 ("Admin exports all
  users → offline PII leak") had been an OPEN P1 finding since the threat
  model was written — no dual-approval workflow existed for large exports
  anywhere in the admin panel. The Compliance & Tax Reporting module
  (`routes/admin/compliance.py`, shipped PR #2650) added two more export
  endpoints — `gst-pst-remittance` and `insurance-period-audit` — that
  return up to `_ROW_LIMIT = 10000` rows each with no gate, silently
  extending the same open risk. Flagged as gap G2 in
  `reports/audits/2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md`.
- **Files:** `backend/migrations/268_admin_export_approvals.sql`,
  `backend/services/admin_export_approvals.py`,
  `backend/routes/admin/export_approvals.py`,
  `backend/routes/admin/compliance.py`,
  `backend/routes/admin/data_transfer_export.py`,
  `admin-dashboard/src/app/dashboard/export-approvals/page.tsx`,
  `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx`,
  `admin-dashboard/src/lib/api.ts`, `admin-dashboard/src/components/sidebar.tsx`.
- **Acceptance:** met — any export > 1,000 rows (Compliance or Data
  Transfer) does not run until a *different* admin approves it from the
  Export Approvals queue; the flag is still off by default, zero behavior
  change until explicitly flipped.
- **Not yet done:** the flag has not been flipped on anywhere (dark-launch
  by design — flip is a separate, deliberate rollout decision, not part of
  this item's acceptance).

### B11. Data Transfer export: no dual-approval gate (extends open AI-3) + PIA recommendations not yet implemented
- [ ] **Status:** in progress (2026-07-29) — R-A through R-F all DONE/resolved.
  The dual-approval gate itself is now DONE (shipped as part of B10 above,
  PRs #2819/#2820 — Data Transfer's `export_entities` route is wired through
  the same shared gate as Compliance). Only R-G remains open, and only
  because it genuinely requires a human privacy/legal determination — a
  self-contained request package for that
  review has been prepared at `reports/legal/data-transfer-implied-consent-review.md`
  (2026-07-28), but the actual determination is still pending a named
  reviewer. Plus the still-open AI-3 dual-approval wiring (shared with B10,
  not specific to this item). The module's P0 gaps (access-control, missing
  PIA) were fixed 2026-07-28 (PRs #2685, #2687); this item tracks the PIA's
  own follow-up recommendations.
  - **R-A DONE:** investigating it before implementing found the original
    finding's premise was wrong — `bulk_operations` was never actually
    grantable to a non-super_admin (not in `AVAILABLE_MODULES`/`ALL_MODULES`/
    any `ROLE_PRESETS`), so access was already super_admin-only in practice,
    just fragile-by-omission. Fixed with an explicit `require_super_admin`
    dependency on all 5 routers instead of splitting a new module flag (the
    new-flag option would have kept the same fragile shape). See
    `docs/change-log/2026-07-28-data-transfer-router-super-admin-gate.md`.
  - **R-B DONE:** added `include_ride_gps`/`include_document_bytes` optional
    flags (both default `True`, unchanged behavior) — ride/document rows
    stay present either way, only GPS coordinates or document byte payloads
    are dropped when opted out. Admin-dashboard: two new checkboxes on the
    Export tab. See `docs/change-log/2026-07-28-data-transfer-export-scope-flags.md`.
  - **R-C DONE:** added a required `reason` field (10-200 chars) to the
    export request, migration 264 (nullable column, application-layer
    "required"), surfaced in the Jobs & History tab's new Reason column and
    in the audit-log metadata. Admin-dashboard: `ExportTab.tsx` (new
    textarea + client-side validation), `JobsTab.tsx` (new column). See
    `docs/change-log/2026-07-28-data-transfer-export-reason-field.md`.
  - **R-D DONE:** also had a wrong premise — no signed URL was ever exposed
    at export time (fully backgrounded route; the "7-day signed URL" was
    computed and immediately discarded, dead code). Removed the dead
    `create_signed_url` call instead of shortening a TTL nothing was exposed
    to. See `docs/change-log/2026-07-28-data-transfer-export-drop-unused-signed-url.md`.
  - All four PIA corrections/updates are reflected in
    `docs/privacy/2026-07-28-pia-data-transfer-export.md` itself (R-001/R-A,
    R-B, R-C, and R-D sections updated in place, not just here).
- **Why:** the Data Transfer export route (`routes/admin/data_transfer_export.py`)
  moves full-fidelity, unredacted PII (government ID numbers, exact GPS ride
  history, identity documents) for up to 100 entities per request with no
  dual-approval gate — the same class of gap as AI-3 (`docs/threat-model/admin-panel.md`)
  and B10 above, extended a second time. Full assessment:
  `docs/privacy/2026-07-28-pia-data-transfer-export.md`; audit trail:
  `reports/audits/2026-07-28-data-transfer-corporate-lifecycle-audit-v1.md`.
- **Action (from the PIA's ranked recommendations):**
  - [HIGH] ~~R-A~~ DONE — see above.
  - [HIGH] ~~R-B~~ DONE — see above.
  - [MEDIUM] ~~R-C~~ DONE — see above.
  - [MEDIUM] ~~R-D~~ DONE — see above.
  - ~~[MEDIUM] R-E: name this module in `docs/runbooks/data-breach.md`.~~ DONE
    2026-07-28 — the runbook already exists (CLAUDE.md's "to be created" note
    was stale; confirmed and corrected 2026-07-28). Added a dedicated §1a-i
    entry naming this module's data flow (full unredacted PII, up to 100
    entities, GPS precision, government IDs) as a designated high-sensitivity
    flow, with containment commands. See PIA doc §7/§8 for detail.
  - ~~[LOW] R-F: confirm `notification_preferences` needs to be in the export
    bundle at all.~~ RESOLVED-AS-IS 2026-07-28 — confirmed and kept. It's
    boolean opt-in/opt-out toggles only (no PII), and the module's stated
    purpose (reconstructing a working account in the target environment)
    genuinely needs it — dropping it would silently revert a migrated
    user's notification settings to defaults on re-import. No code change.
    Reasoning documented in the PIA doc §8.
  - [LOW] R-G: formal legal review of the implied-consent basis for this
    secondary use — **needs a human privacy/legal sign-off, not resolvable
    by an engineering task.** Still open; do not mark done without an actual
    reviewer name + date in the PIA's Section 9 sign-off table. **Request
    prepared 2026-07-28:** `reports/legal/data-transfer-implied-consent-review.md`
    packages the specific question, background, and what a closed-out review
    should record, following the house format of
    `reports/legal/supabase-region-attestation-checklist.md`. Also flags two
    facts not fully surfaced in the PIA itself: (1) `docs/legal/privacy-policy.md`
    (still unpublished) currently has no language covering this internal
    cross-environment data-movement use case — if legal concludes a distinct
    disclosure is needed, that draft is the place to add it before first
    publication; (2) the module's transfer stays entirely within Spinr's own
    Supabase project (no third-party recipient), which the request flags as
    relevant to the reasonable-secondary-use analysis. No privacy-officer/legal
    role is currently assigned in this repo to actually make the call — see
    the request's Status table.
  - When AI-3's shared dual-approval mechanism is built (see B10), wire this
    route through it too rather than a one-off gate.
- **Files:** `backend/routes/admin/data_transfer_export.py`,
  `backend/routes/admin/data_transfer_jobs.py`, `backend/migrations/`
  (new `reason` column for R-C), `docs/threat-model/admin-panel.md`
  (AI-3 row updated to reference this scope).
- **Acceptance:** not gating — acceptance is AI-3's own, same as B10, plus
  each PIA recommendation's own stated success criterion (see the PIA doc
  §8 for R-A through R-G).

### B12. Corporate billing: race-test coverage gaps and no compensating-transaction runbook
- [x] **Status:** DONE (2026-07-28, branch `claude/b12-corporate-coverage-runbook`)
  — the P0 gap (no regression test for the migration-258 allowance-cap race)
  was fixed 2026-07-28 (PR #2686); both remaining P1 items from the same
  audit are now closed:
  - ~~runbook~~ `docs/runbooks/corporate-compensating-transaction.md` written
    — detection via the ledger, target-balance compensating-delta computation
    (not a blind reversal), applying the correction through the same locked
    RPC, and reconciliation queries.
  - ~~coverage gap~~ all four files now ≥90% (measured via
    `pytest --cov=routes.corporate_rider --cov=routes.corporate_company_bookings
    --cov=routes.corporate_accounts --cov=routes.corporate_company
    --cov-report=term-missing backend/tests/ -k corporate`, 503 passed / 3
    skipped / 0 failed):
    `routes/corporate_rider.py` 65% → **96%**,
    `routes/corporate_company_bookings.py` 57% → **94%**,
    `routes/corporate_accounts.py` 79%/82% → **97%**,
    `routes/corporate_company.py` 79% → **93%**.
- **Why:** `reports/audits/2026-07-28-data-transfer-corporate-lifecycle-audit-v1.md`
  found: (1) no compensating-transaction runbook exists for a bad
  `corporate_wallet_apply_delta`/`corporate_allowance_apply_delta`
  application — the documented rollback ("drop the function") doesn't undo
  money already moved; (2) four corporate route files remain below the 90%
  money-path coverage floor: `routes/corporate_rider.py` (65%),
  `routes/corporate_company_bookings.py` (57%),
  `routes/corporate_accounts.py` (79%), `routes/corporate_company.py` (79%).
- **Action:** write a concrete compensating-transaction runbook (mirrors the
  CLAUDE.md rule that money deltas need more than `git revert`); raise the
  four listed files' coverage, prioritizing branches that touch
  allowance/wallet reads. Also verify KYB document Storage bucket RLS/access
  scoping (not confirmed in the audit) and track the v2-deferred corporate
  scope (cost centers, approval workflows, SSO/HRIS — currently only
  discoverable via `docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md`)
  here so it isn't lost.
- **Still open (out of scope for this pass, tracked here per the audit's own
  note):**
  - KYB document Storage bucket RLS/access scoping — flagged "not confirmed"
    by the audit; needs its own security-focused pass, not attempted here.
  - v2-deferred corporate scope (cost centers, approval workflows, SSO/HRIS)
    — see `docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md`.
- **Files:** `backend/services/corporate_wallet_service.py`,
  `backend/services/corporate_allowance_service.py`,
  `backend/routes/corporate_rider.py`, `backend/routes/corporate_company_bookings.py`,
  `backend/routes/corporate_accounts.py`, `backend/routes/corporate_company.py`,
  `docs/runbooks/corporate-compensating-transaction.md` (new),
  `backend/tests/test_corporate_rider_routes.py` (extended),
  `backend/tests/test_corporate_company_bookings_routes.py` (new),
  `backend/tests/test_corporate_sections.py` (extended),
  `backend/tests/test_corporate_accounts_lifecycle.py` (new),
  `backend/tests/test_corporate_company_gap_coverage.py` (new).
- **Acceptance:** runbook exists and is concrete/testable (not just "revert
  the commit") — met; all four listed files reach ≥90% coverage — met.

### B13. 22 drivers have no `regulatory_authority`/`regulatory_region` set (blocks the SGI-forms segregation guard from covering them)
- [x] **Status:** backfill done (2026-07-28, migration
  `265_drivers_regulatory_authority_backfill.sql`) — all 22 rows verified
  by `id` against the real project (`soavhtdhefowwvforzwb`) and confirmed
  to resolve to `service_areas` 'Regina' or 'Saskatoon' (both real
  Saskatchewan markets) before backfilling
  `regulatory_authority='SGI', regulatory_region='SK'`. Post-migration:
  `SELECT count(*) FROM drivers WHERE regulatory_authority IS NULL` → `0`;
  all 209 drivers are now `SGI`/`SK`. `routes/admin/sgi_forms.py` still
  hard-blocks generating an SGI D00032/D00033 for any driver whose
  `regulatory_authority` is explicitly set to something other than `"SGI"`
  (Alberta-expansion safety guard, unchanged by this backfill).
- **Why (original gap):** confirmed directly against the real staging schema
  (`SELECT regulatory_authority, regulatory_region, count(*) FROM drivers
  GROUP BY 1, 2`): 187 rows were `SGI`/`SK`, 21 fully NULL, 1 had
  `region=SK` but `authority=NULL` — all real Saskatchewan drivers (no
  Alberta data existed yet), so this was a backfill gap, not a
  misclassification.
- **Remaining action (guard tightening, not yet done):** now that the
  backfill is complete, `sgi_forms.py`'s `_out_of_scope_drivers()` can be
  tightened to require an explicit `regulatory_authority == "SGI"` match
  instead of treating NULL as in-scope — but hold off until Alberta's own
  `driver_import_service` onboarding path is confirmed to always populate
  the field for new AB drivers (otherwise the tightened guard would start
  blocking legitimate new AB drivers' own province-specific forms once
  those exist, not just protect against cross-province mixing). Not
  blocking: since 100% of drivers are now non-NULL, the NULL-passes
  branch is currently dead code in practice — the risk it originally
  covered (a NULL Alberta driver slipping through) can't happen yet
  because no Alberta driver data exists, and re-tightening is a small,
  isolated follow-up whenever Alberta onboarding actually starts.
- **Related, separate gap (not part of B13, not fixed here):**
  `service_areas.province`/`regulatory_authority`/`regulatory_region` are
  still NULL for 'Saskatoon', 'Regina Airpot', 'riyadh', and
  'riyadh airport' (only 'Regina' is populated) — noticed while verifying
  this backfill's source data. Doesn't block anything today (this
  migration keyed off verified driver `id`s directly, not the
  service_areas reference columns) but is a latent gap if any future code
  starts trusting `service_areas.regulatory_authority` as a source of
  truth for those areas.
- **Files:** `backend/routes/admin/sgi_forms.py` (unchanged — tightening
  still pending), `backend/migrations/265_drivers_regulatory_authority_backfill.sql`
  (new, applied).

### B14. SGI form company address split across dedicated fields + driver licence-number/class data gap
- [x] **Status:** address bug DONE (2026-07-29). Licence-number/class
  confirmed as a genuine **data gap**; a third, independent bug was found
  and fixed while building the remediation tool (admin driver-edit route
  wrote `license_number` as plaintext instead of Vault-encrypting it —
  see below). The backfill **queue/tooling is now built and live**
  (`/dashboard/driver-license-backfill`); the actual 22-driver data entry
  is a manual step for an admin to do in that screen, not something this
  session can perform (requires reading real government ID photos). The
  larger OCR/automated-onboarding proposal is written up but **not
  started**, pending a scope/vendor decision.
- **Why (address):** both real SGI templates (`D00032`/`D00033`) ship
  dedicated `Street address`/`City/town`/`Provincestate`/`Postalzip code`
  fields (confirmed via `PdfReader.get_fields()`), but
  `sgi_form_filler.py` was setting only the street-address field to one
  combined `"STREET, CITY, PROVINCE, COUNTRY, POSTAL"` string, leaving the
  template's own dedicated city/province/postal fields at their stale
  placeholder values — every generated form showed two disagreeing
  addresses across its own fields. Fixed: address split into
  street/city/province/postal constants, each mapped to its correct
  field; country dropped (neither template has a field for it). Two
  regression tests assert no city/province/postal/country string leaks
  into the street field.
- **Why (licence number/class):** traced field-mapping, PDF-slot naming,
  and Vault decryption end-to-end — all correct. Checked the real
  `drivers` table directly: 22 of 209 drivers (some already
  `is_verified: true`) have `NULL` `license_number`/`license_class`. Root
  cause: these are optional self-serve profile fields, never required at
  signup or at document-review approval, and the driver's-license photo
  each of these drivers *did* upload during onboarding is never OCR'd to
  populate the structured columns — an admin has to manually retype it,
  and nothing prompts that. Full analysis, immediate-remediation steps,
  and a reasoned automated-onboarding (OCR + capture-guidance +
  dual-approval) proposal in
  `docs/proposals/2026-07-29-driver-document-ocr-onboarding-automation.md`.
- **Third bug found + fixed while building remediation:** `routes/admin/
  drivers.py`'s `PUT /admin/drivers/{id}` (the exact endpoint the backfill
  tool needed to write through) wrote `license_number` as plaintext —
  unlike the self-serve profile-update and bulk-import paths, which both
  correctly call `_encrypt_driver_pii()` first. Any admin editing a
  driver's licence number via the dashboard was storing it unencrypted, a
  PIPEDA violation per that module's own docstring. Fixed before building
  anything on top of that endpoint, with a regression test asserting the
  raw value never reaches the DB write.
- **Immediate remediation — tooling DONE, data entry still open:** (1)
  `/dashboard/driver-license-backfill` (new admin page) lists exactly the
  drivers missing licence data via a new `missing_license` filter on
  `GET /admin/drivers`, lets an admin open the existing `DocumentReviewer`
  to view each driver's already-uploaded licence photo, and save via the
  now-fixed encrypting update path — an admin still needs to actually work
  through the queue (this session cannot reliably read government ID
  photos); (2) make licence-number/class entry a required part of the
  admin document-review "approve" action going forward, so this gap can't
  grow — small scoped change, still open, own PR + Change Impact Log (it
  changes an existing live admin workflow).
- **Larger proposal (not started, needs a decision):** OCR-assisted
  document intake with client-side capture guidance (Expo camera +
  quality gate), a purpose-built ID-OCR vendor (buy, not build — see
  proposal's reasoning), and a human dual-approval queue reusing the same
  state-machine shape as B10's export-approval gate. Recommends
  email/SMS notification-channel parity (today: push-only on document
  rejection, no channel at all on upload-received) as the fastest,
  vendor-independent first slice. See the proposal doc for full reasoning,
  PIPEDA precautions, and sizing.
- **Files:** `backend/services/data_transfer/sgi_form_filler.py` (address
  fix), `backend/tests/test_sgi_form_filler.py` (2 new regression tests),
  `backend/routes/admin/drivers.py` (encrypt-on-write fix + `missing_license`
  filter), `backend/tests/test_admin_business_logic.py` (encryption
  regression test), `backend/tests/test_admin_extended.py` (2 filter
  tests), `admin-dashboard/src/app/dashboard/driver-license-backfill/page.tsx`
  (new), `admin-dashboard/src/lib/api.ts`, `admin-dashboard/src/components/sidebar.tsx`,
  `docs/proposals/2026-07-29-driver-document-ocr-onboarding-automation.md`
  (new).
- **Acceptance (address, done):** generated D00032/D00033 PDFs have each
  address component in its correct dedicated field, verified by
  regenerating both forms and reading every field back. **Acceptance
  (encryption fix, done):** regression test confirms plaintext never
  reaches the DB write. **Acceptance (licence data, pending):** not
  gating — tracked here until an admin actually works through the
  `/dashboard/driver-license-backfill` queue and a decision is made on
  the larger proposal.

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

### C5. Re-enable Railway standby deploys (currently paused)
- [ ] **Status:** open — `deploy-backend.yml` (Railway) is deliberately blocked via a
  GitHub Environment protection rule (confirmed 2026-07-27). This was meant to be
  **temporary** but has no expiry/owner attached, so Railway has been silently
  drifting from `main` since the pause started — contradicts ADR-007's "hot standby,
  not a paper fallback" design, and means a Fly outage right now would fail over to
  a stale (possibly vulnerable, possibly schema-mismatched) build.
- **Action:** confirm the original reason for pausing no longer applies, then remove
  the Environment protection rule (or update its required reviewers/branch
  restriction) so `deploy-backend.yml` resumes auto-deploying on every push to
  `main`. Before flipping it back on: verify secret parity between Railway and Fly
  per ADR-007's own risk section (`JWT_SECRET`, `SUPABASE_*`, `FIREBASE_*`, Redis
  URLs), since Railway's env vars may also have drifted during the pause. Re-run
  the failover drill (C1) afterward to confirm the standby actually works end to
  end, not just that it deploys.
- **Owner / follow-up:** none assigned yet — flag in the next planning sync so this
  doesn't become a permanently-forgotten "temporary" gap.

### B-AI1. Corporate rider booking via AI chat bypasses corporate billing
- [x] **Status:** done (2026-07-29) — found by the 2026-07-28 AI guardrail
  audit (branch `claude/rider-ai-location-selection-yn0mem`), fixed on branch
  `claude/b-ai1-corporate-billing-chat`. The in-chat booking card always
  booked with `corporateAccountId=undefined`:
  `rider-app/components/BookingProposalCard.tsx:155-159` called
  `createRide(paymentMethod, undefined, ...)` →
  `rider-app/store/rideStore.ts` sent `corporate_account_id: null,
  work_profile: null`, so corporate policy checks
  (`backend/routes/rides/booking.py:717-721`) never ran and the ride billed
  the rider personally. Only **wallet**-payment proposals booked inline (card
  proposals deep-link to `/ride-options`, where Bill-to-Business worked), so
  the exposure was corporate riders who said "pay with wallet" in chat.
- **Approach chosen (user confirmed via `AskUserQuestion` — see
  `docs/change-log/2026-07-29-b-ai1-corporate-billing-chat-bypass.md` §3):**
  mirror `/ride-options.tsx`'s own default — if the rider's Work Mode toggle
  (`useWorkProfileStore`) is on with an active company, book to that company
  by default, same as the standard screen already does for the same rider
  state. Not a new payer-selection design; the two rejected alternatives
  (force to `/ride-options` unconditionally, or add a new explicit
  payer-picker UI) are recorded in the change-log for reference if revisited.
- **Fix:** `BookingProposalCard.tsx` now reads `workModeEnabled`/
  `activeCompanyId`, computes `corporateAccountId` the same way
  `/ride-options` does, runs the same `checkRide()` client-side policy
  pre-check before booking (blocking with the policy-violation reason
  instead of silently booking), passes the id to `createRide`, and shows a
  "Charged to `<Company>`" pill on the card so the payer is visible before
  the rider confirms.
- **Files:** `rider-app/components/BookingProposalCard.tsx`,
  `rider-app/__tests__/bookingProposalCardCorporate.test.tsx` (new, 4 tests),
  `rider-app/__tests__/bookingProposalCardPromo.test.tsx` (added a
  `workProfileStore` mock stub — unrelated to that test's own assertions,
  needed once the component started importing the store).
- **Acceptance:** ✅ met — a corporate rider booking via AI chat with Work
  Mode on now gets the same payer (and the same client-side policy
  pre-check) as `/ride-options`; regression tests pin Work-Mode-off (no
  change), Work-Mode-on (books to company + shows the pill), policy-failure
  (blocks + shows the reason), and card-path-unaffected. Full `rider-app`
  suite re-run: 51 suites / 434 tests passed. `tsc --noEmit` clean.
- **Not verified (see change-log §10 for full list):** the real production
  build CLAUDE.md requires for `rider-app` was attempted
  (`expo export --platform web`) but fails before reaching any app code, on
  a pre-existing environment-level `react-native-fbsdk-next` config-plugin
  resolution error unrelated to this diff (same known issue as this
  session's rider/driver-app E2E CI noise) — not skipped, but not a passing
  build either. `tsc --noEmit` (clean) + full Jest suite (434/434) are the
  strongest verification available here. Also not exercised against a real
  backend/Supabase instance or a live corporate membership.

## P3 — Post-launch backlog (tracked, not gating)

### AI assistant / MCP guardrail backlog (2026-07-28 audit, branch `claude/rider-ai-location-selection-yn0mem`)

_Implemented from the same audit (do not redo): tapped-suggestion coordinate
plumbing (rider + admin console), never-re-ask-twice + no-internal-jargon +
driver-persona-secrecy prompt rules, per-tool timeouts for the Maps fan-out
tools, `/mcp` read-only enforcement + per-user daily cap, truncation-preserves-
guardrail-notes, threat-flagged turns excluded from the FAQ cache. Remaining:_

- [ ] **AI1. `/ai/chat` rate limit is per-IP, not per-user** —
  `backend/routes/ai.py:130` uses `ai_chat_limit` keyed on client IP
  (`utils/rate_limiter.py:111-118`); the per-user daily cap fails OPEN on
  Redis errors (`backend/ai/orchestrator.py:82-84`). One user on many IPs, or
  a Redis blip, removes the LLM-cost ceiling (kill switch remains the hard
  stop). Consider a user-keyed limiter + fail-closed above a generous floor.
- [ ] **AI2. Assistant output is persisted un-scrubbed** — only the user
  message passes `scrub_pii` (`orchestrator.py:145`); assistant text is
  streamed and stored raw in `ai_messages`, asymmetric with
  `conversations.py`'s stated contract and Sentry's strict scrubbing.
- [ ] **AI3. No cap on parallel tool calls per iteration** —
  `orchestrator.py` gathers all requested calls unbounded (6 iterations ×
  N calls, each able to hit Google Maps). Cap per-iteration fan-out (e.g. 5).
- [ ] **AI4. `scheduled_time` reaches the proposal unvalidated** —
  `tools_booking.py` accepts any ≤80-char string; a hallucinated/past ISO
  time renders on the card and only fails at Confirm. Validate ISO-8601 +
  ≥5-min lead at proposal time.
- [ ] **AI5. `find_place` offers out-of-service-area street addresses** —
  the area filter is skipped for street-address-shaped queries
  (`tools_booking.py:538-539`), so a rider can pick a location the booking
  step later refuses. Filter (or visibly mark) out-of-area candidates for
  street queries too.
- [ ] **AI6. No handling for pasted Google Maps URLs / raw coordinates** —
  bare `lat,lng` is scrubbed to `[COORDS]` before the model sees it
  (`pii.py:33`) with no prompt rule for the token; short links carry no
  coordinates and get text-searched as URL strings.
- [ ] **AI7. Multilingual gap** — no language rule in prompts; Maps calls
  hard-code `language: "en"`; FAQ keyword matching is English-only.
- [ ] **AI8. Stale action cards never expire client-side** — every past
  quote/suggestion/map-pin card stays tappable
  (`rider-app/app/ai-assistant.tsx`); backend self-contained messages
  mitigate, but a stale-yet-consistent quote re-books at a possibly different
  price. Consider disabling cards older than the latest assistant turn.
- [ ] **AI9. Admin AI console quote-card tap still prose-only** — same
  defect class fixed for suggestions;
  `admin-dashboard/.../ai-console/page.tsx:125-131` drops `[lat,lng]` and
  vehicle id. Move `buildQuoteBookingMessage` into
  `shared/utils/aiLocationMessages.ts` (re-export shim in
  `rider-app/components/bookingProposal.ts`) and use it in the console.
- [ ] **AI10. No conversation-level concurrency lock server-side** — two
  clients on one `conversation_id` interleave `append_message` writes and
  race history snapshots (client is single-flight only).
- [ ] **AI11. Cancel-ride escalation UX** — the assistant correctly refuses
  to cancel rides, but there is no `cancel`/`ride_issue` escalation category
  and no deep link to the ride screen — riders get a support ticket for a
  self-serve action.
- [ ] **AI12. Admin console endpoint has no rate limiter and a stale
  docstring** — `routes/admin/ai_console.py` claims turns count against the
  daily cap; the orchestrator deliberately exempts them, and the endpoint has
  no `@ai_chat_limit` equivalent (super-admin-only + audited, so low risk).
- [ ] **AI14. Accepted risk: a tapped suggestion is trusted even when its
  geocode is only APPROXIMATE** — prompt rule 6b (PR #2774) treats any
  rider-tapped `location_suggestions` candidate as confirmed, so a numbered
  street address Google could only resolve to a street/neighbourhood centroid
  can be quoted and booked at that centroid rather than the building.
  `_dropoff_pair_refusal` does not catch this (it is a label-vs-pin
  *consistency* check biased near the passed pin, not a precision check).
  **This is deliberate**, not an oversight: the alternative — routing
  imprecise taps through `request_map_pin` — was considered and rejected for
  this iteration because it adds a step for every rider and degrades to a
  dead end on clients that don't advertise the `map_pin` capability
  (`tools_booking.py` returns `shown: False` there), which is the exact
  no-exit state that produced the original infinite loop. Raised by Codex
  review on PR #2774 (`backend/ai/prompts.py:96`).
  **Middle ground if this is revisited:** have `find_place` surface
  `precise=False` on the card and let the assistant quote immediately while
  offering the map pin as an optional refinement (the "quote + note" option),
  rather than gating the quote.
- [ ] **AI13. No output-side leakage filter** — prompt rules (added
  2026-07-28) are the only defense against the model printing tool names /
  internal jargon; nothing greps the reply stream. A lightweight post-filter
  for snake_case tool names in assistant text would make the secrecy rule
  structural.

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
- [ ] **D9. `compliance_export_events` has no purge job for its claimed 7-year
  retention** — `backend/migrations/263_compliance_export_events.sql`'s table
  comment states "7-year retention" but no background loop or scheduled job
  enforces it; rows accumulate forever today. Long time horizon (first purge
  wouldn't be due until 2033), so not urgent, but the claim in the migration
  comment currently overstates what the system actually does — nothing
  deletes a row past 7 years yet. Migration itself is append-only and merged
  (can't be edited per `backend/migrations/CLAUDE.md`); the fix is a new
  migration/cron adding a scheduled purge (mirror the pattern in
  `utils/retention_purge.py`) before 2033, not a comment edit. Tracked here
  as gap G8 from `reports/audits/2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md`.
- [x] **D10. `compliance_export_events` rollback command not re-verified
  against real staging** — `DROP TABLE IF EXISTS compliance_export_events;`
  (the migration's documented rollback) was verified by applying the
  migration to the local dev clone and dropping it there, never against the
  real staging Supabase project (`spinrmobileapp`) the migration was
  actually applied to during PR #2675's smoke test. **Status:** accepted as
  sufficient, not re-verified — per the audit's own framing (gap G10), this
  is optional given the table currently holds zero real rows in staging
  (confirmed during that same smoke test), so a `DROP TABLE IF EXISTS` on an
  empty table is exceedingly unlikely to behave differently in staging than
  locally. Re-running a destructive `DROP TABLE` against the shared staging
  project purely to prove a rollback command — without a concrete need to
  actually roll back — is not worth the risk for a statement this
  well-supported already; CLAUDE.md's guidance on destructive/hard-to-reverse
  actions favors skipping an unnecessary one over running it "just to be
  sure." Revisit if the table ever holds real data before this is re-verified.

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
| Data Transfer job-history endpoints (bulk_operations flag → super_admin, closed cross-admin PII exposure) | `88d9c51` (PR #2685) |
| Corporate allowance-cap race regression test (no test existed for the migration-258 double-spend fix) | `4257690` (PR #2686) |
| PIA for Data Transfer export path (none existed for this PII-moving flow) | `48d2d0f` (PR #2687) |
