# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | rides / drivers / corporate / admin / ai |
| PR / commit link | (this branch: `claude/spinr-ai-guardrail-reviewer-o2vups`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier B (full sweep) |

## 1. Issue / gap identified

A1c's Sub-tier B (below-60%-coverage, lower-risk breadth files) listed 26
files with no dedicated test file, ranging from `routes/main.py` at 0% to
`utils/document_expiry.py` at 58.71%. Only `utils/reconciliation.py` had
previously been picked out of this tier (closed in an earlier session).
The rest sat untouched.

## 2. Root cause

None of these 26 files had a dedicated unit-test file; whatever coverage
existed was an incidental side effect of other modules' tests exercising
them indirectly (e.g. via shared fixtures or integration-style route
tests). Several are genuinely important despite their "Sub-tier B"
label — `routes/main.py` is the literal `/health` endpoint Railway's
readiness probe and the post-deploy smoke test (A2) depend on;
`core/lifespan.py` is the central startup/shutdown orchestrator for all 17
background loops; `repositories/corporate_repo.py` is money-adjacent
(corporate billing); several others are background loops in their own
right (`utils/scheduled_rides.py`, `utils/stuck_ride_sweeper.py`,
`utils/document_expiry.py`, `utils/zoho_desk_sync.py`).

## 3. Fix / remediation

Test-only change. Added 26 new test files (one per source file, listed in
full in section 6), covering every file in A1c's Sub-tier B list. Work was
split across direct authoring and briefed subagents (each subagent was
given the same established conventions — dual-import-safe patching, the
Supabase chaining-query-mock pattern, and the `asyncio.sleep`-raises-
`CancelledError` background-loop testing pattern — and instructed not to
run tests, since this whole sweep was written and verified as one batch
per the session's own working style). All 26 files were then verified
together in a single full-suite pass; 17 tests initially failed and were
individually root-caused and fixed (see section 9's verification detail
and the "bugs found in the tests" note below) before this log was written.

**No application code was changed anywhere in this sweep.** Several
findings worth a human's attention were surfaced and are called out below,
consistent with this repo's "flag, don't silently fix" convention for a
test-only pass:

- `routes/users.py`: `DELETE /users/profile` is documented as
  "Permanently delete the current user's account and all associated
  data" but only soft-deletes the `users` row (`update_one(...,
  {"deleted_at": now})`) — behaviorally near-identical to the explicitly
  soft-delete `DELETE /users/account` (`delete_account_pipeda`). Worth
  confirming which endpoint the rider app's "Delete my account" flow
  actually calls.
- `utils/push_retry.py`: `_process_row` bumps `attempts`/
  `next_attempt_at` via the atomic claim *before* attempting delivery. If
  delivery succeeds but the subsequent `sent_at` UPDATE itself raises,
  the row's `sent_at` stays NULL and becomes due again after the
  back-off — an at-least-once (possible duplicate-push) characteristic,
  pre-existing, not introduced by this pass.
- `utils/scheduled_rides.py`: `_dispatch_scheduled_ride`'s outer
  `except Exception` on the claim call logs loudly but gives
  `check_scheduled_rides`'s per-ride loop no distinct signal to
  distinguish "transient DB error, should retry sooner" from
  "legitimately already claimed."
- `routes/support.py` (rider/driver AI chat): `support_chat`'s single
  broad `except Exception` converts *any* Gemini SDK failure (bad key,
  quota exhaustion, network error, SDK bug) into a 200 OK fallback reply
  with only a `logging.warning` — no Sentry, no `domain`/`surface` tags,
  no error-level log — masking a real outage as an ordinary chat answer.
  This is the one finding in this sweep that most directly conflicts with
  CLAUDE.md's "never silently swallow DB/auth/payment/dispatch errors"
  convention (chat isn't one of those four categories verbatim, but the
  masking pattern is the same shape).
- `utils/redis_client.py`-adjacent: none new in this pass (already closed
  earlier the same day).

None of these were fixed in this PR — they are test-only findings flagged
for a follow-up decision, per this repo's established "found, not fixed"
convention for coverage-only passes.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** 26 new test files only; zero application
  code changed across the entire sweep. Every test file's dependencies
  are mocked at the module boundary (Supabase client, Redis, Stripe SDK,
  httpx/network calls, FCM/Expo push sends, Google Generative AI SDK) —
  no real I/O anywhere.
- **`core/lifespan.py` care**: this is the one file in the sweep where a
  testing mistake could have real consequence (issue #2981's original
  bug — spawning real background loops during the test suite). The new
  tests explicitly regression-test the `ENV=="test"` no-op guard (patching
  the real stdlib `asyncio.create_task` and asserting it's never invoked
  for any of the 17 loop names) rather than assuming it holds, and one
  test that would have violated this (entering `lifespan()` under
  `ENV=production`, which really would have spawned all 17 loops) was
  deliberately NOT written — documented in the test file as a "why not"
  rather than silently omitted.
- **Money-adjacent files** (`repositories/corporate_repo.py`): test-only,
  no changes to `corporate_wallet_apply_delta` or any RPC call site;
  Decimal boundary cases (e.g. `balance == threshold` must NOT trigger
  auto-topup) are asserted, not altered.
- **A real, root-caused bug in the test infrastructure itself** was found
  and fixed during verification (not application code): a `_dual_patch`
  helper in `test_routes_main_coverage.py` patched the same underlying
  attribute twice (via two module-name spellings that, for
  `routes.main`/`backend.routes.main` specifically, resolve to the
  identical module object in this test environment) using raw
  `unittest.mock.patch()` + manual `.stop()` calls in FIFO order — which
  corrupts the restore when the same target is patched twice, leaving a
  `side_effect=RuntimeError(...)` mock permanently bound to
  `utils.loop_monitor.get_loop_status`. This silently broke 5
  *pre-existing* tests in `tests/test_webhooks_main.py::TestLoopMonitor`
  the moment `test_routes_main_coverage.py` ran before them in the same
  session (order-dependent, would not reproduce running either file
  alone). Fixed by switching to pytest's `monkeypatch.setattr`, which
  correctly restores the true original even when the same target is set
  multiple times, and by deduping same-object module targets before
  patching. See the fix commentary in `test_routes_main_coverage.py`'s
  `_dual_patch` docstring for the full mechanism.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed |
|---|---|
| `backend/tests/test_routes_main_coverage.py` | New — `routes/main.py` (0% → 84.6%) |
| `backend/tests/test_t4a_pdf_coverage.py` | New — `utils/t4a_pdf.py` (4.40% → 97.8%) |
| `backend/tests/test_subscription_invoice_pdf_coverage.py` | New — `utils/subscription_invoice_pdf.py` (7.97% → 99.3%) |
| `backend/tests/test_zoho_desk_db_coverage.py` | New — `services/zoho_desk_db.py` (11.76% → 99.2%) |
| `backend/tests/test_demand_forecast_coverage.py` | New — `utils/demand_forecast.py` (18.52% → 98.8%) |
| `backend/tests/test_zoho_desk_sync_coverage.py` | New — `utils/zoho_desk_sync.py` (22.33% → 95.2%) |
| `backend/tests/test_analytics_coverage.py` | New — `utils/analytics.py` (22.70% → 98.2%) |
| `backend/tests/test_lost_and_found_route_coverage.py` | New — `routes/lost_and_found.py` (25.85% → 89.1%) |
| `backend/tests/test_stripe_kyc_sync_coverage.py` | New — `services/stripe_kyc_sync.py` (30.70% → 97.4%) |
| `backend/tests/test_marketing_push_coverage.py` | New — `utils/marketing_push.py` (33.33% → 100%) |
| `backend/tests/test_ws_pubsub_coverage.py` | New — `utils/ws_pubsub.py` (38.46% → 100%) |
| `backend/tests/test_bundle_document_uploader_coverage.py` | New — `services/data_transfer/bundle_document_uploader.py` (38.75% → 100%) |
| `backend/tests/test_routes_users_coverage.py` | New — `routes/users.py` (39.86% → 93.6%) |
| `backend/tests/test_routes_support_coverage.py` | New — `routes/support.py` (42.22% → 88.9%) |
| `backend/tests/test_corporate_repo_coverage.py` | New — `repositories/corporate_repo.py` (42.29% → 99.4%) |
| `backend/tests/test_push_retry_coverage.py` | New — `utils/push_retry.py` (45.30% → 98.3%) |
| `backend/tests/test_maps_proxy_coverage.py` | New — `routes/maps_proxy.py` (51.35% → 83.8%) |
| `backend/tests/test_route_validation_coverage.py` | New — `utils/route_validation.py` (53.33% → 100%) |
| `backend/tests/test_scheduled_rides_coverage.py` | New — `utils/scheduled_rides.py` (55.40% → 93.5%) |
| `backend/tests/test_suspension_reactivation_coverage.py` | New — `utils/suspension_reactivation.py` (55.93% → 94.9%) |
| `backend/tests/test_route_snapshot_coverage.py` | New — `utils/route_snapshot.py` (57.08% → 99.1%) |
| `backend/tests/test_stuck_ride_sweeper_coverage.py` | New — `utils/stuck_ride_sweeper.py` (57.32% → 90.2%) |
| `backend/tests/test_core_security_coverage.py` | New — `core/security.py` (57.89% → 100%) |
| `backend/tests/test_core_lifespan_coverage.py` | New — `core/lifespan.py` (58.52% → 64.3%) |
| `backend/tests/test_routes_marketing_coverage.py` | New — `routes/marketing.py` (58.57% → 94.3%) |
| `backend/tests/test_document_expiry_coverage.py` | New — `utils/document_expiry.py` (58.71% → 91.6%) |
| `docs/change-log/2026-08-02-a1c-subtier-b-sweep-coverage.md` | New (this log) |
| `ACTION_ITEMS.md` | A1c Sub-tier B section updated: all 26 files marked done |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing
diff. See the coverage percentages in section 6.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Full backend suite run after all 26 files landed and the first round
  of fixes applied: `pytest tests/ -q --no-cov` → **7808 passed, 8
  skipped, 1 xfailed, 0 failed** (up from a pre-sweep baseline in the
  7100s; exact delta not separately recorded since this was one
  continuous session). See the second verification pass below for a
  follow-up round after rebasing onto newer `main`.
- [x] Coverage measured for all 26 target files in one combined run
  (`pytest tests/ --cov=<26 module paths> --cov-report=term-missing`,
  cross-checked against `coverage.xml`) — see section 6 for the
  before/after per file. Aggregate for the 26-file set: mid-80s%–100%
  except `core/lifespan.py` (64.3% — see "what was not verified" below)
  and `routes/maps_proxy.py` (83.8%, already had a pre-existing sibling
  test file this pass deliberately avoided duplicating).
- [x] 17 test failures found on the first full-suite run were individually
  root-caused (not silenced) and fixed — five distinct bug classes, all in
  the NEW test files or (one case) the test infrastructure, never in
  application code:
  1. A `unittest.mock.patch()` FIFO-teardown-order bug in
     `test_routes_main_coverage.py`'s dual-patch helper (see section 4) —
     fixed by switching to `monkeypatch.setattr`.
  2. A `core/lifespan.py` test patching a module-level `asyncio` attribute
     that doesn't exist (the function imports `asyncio` locally) — fixed
     by patching the real stdlib `asyncio.create_task` directly.
  3. Two `corporate_repo.py` search-sanitization tests asserting no
     comma/paren anywhere in the constructed PostgREST OR-clause, when
     those characters are legitimate structural separators in the clause
     template itself — only the embedded search term should be
     sanitizer-checked. Fixed the assertions to extract just the
     sanitized term.
  4. Two `demand_forecast.py` tests with the averaging formula backwards
     (`len(dates) / unique_days` is rides-per-day, not the reverse) — my
     own authoring mistake from earlier in the session, fixed to match
     actual (correct) source behavior.
  5. One `lost_and_found.py` test with a wrong mock `side_effect` list
     length (missing the leading `_driver_for_user` lookup call), one
     `maps_proxy.py` test asserting an exact rounded-coordinate string
     without accounting for float binary-representation imprecision
     (`round(52.12345, 4)` is `52.1234`, not `52.1235`), two
     `push_retry.py` `_claim_row` tests that mocked `run_sync` to return
     the raw query response directly — bypassing the inner closure's own
     `bool(...)` conversion entirely — fixed to let `run_sync` actually
     execute the closure it's given, and one `route_snapshot.py` test
     with a marker-count arithmetic error in its own comment (2+2+2=6,
     asserted as 8).
- [x] Blast-radius consideration: every test file's mocking boundary was
  chosen at the module's own dependency seam (Supabase client, Redis,
  Stripe/Google/Zoho SDKs, push senders) — no shared test fixture or
  conftest.py logic was modified.
- [x] **Concurrent-session drift, second pass.** This branch's prior commit
  history was already squash-merged into `main` (PR #3243), so per
  CLAUDE.md's guidance the branch was restarted from latest `main` before
  finalizing this sweep. That pickup landed 30 commits of other sessions'
  concurrent work, three of which changed source files this sweep already
  had tests for: `repositories/corporate_repo.py`'s search now goes
  through the shared `_apply_filters`/`_build_or_clause_term` $regex path
  (escapes reserved characters instead of stripping them — PR #3289,
  "Corporate + admin portal review"), `utils/demand_forecast.py` renamed
  its `confidence` field to `data_basis` with new value strings (same PR,
  Admin #3 — the old name overstated the rigor of a plain historical
  average), and `services/data_transfer/bundle_document_uploader.py` fixed
  the exact MIME-type bug this sweep's own tests had originally flagged
  (declared type now derived from the file extension instead of a
  hardcoded wrong value). Each was individually re-verified against the
  new source (not assumed) and the affected tests updated to match —
  2 tests in `corporate_repo`, 6 in `demand_forecast`, 1 in
  `bundle_document_uploader` (that last one flipped from "pins the bug"
  to "confirms the fix," since the bug no longer exists). Coverage was
  re-measured for all three after the update: `corporate_repo.py` 99.4%,
  `demand_forecast.py` 98.8%, `bundle_document_uploader.py` 100% — all
  three held steady or improved versus the numbers in section 6.
  Full suite re-run clean after: **8374 passed, 8 skipped, 1 xfailed, 0
  failed** (test count rose from 7808 due to the additional tests picked
  up from concurrent sessions' own merged work, not from anything in this
  sweep).

## 10. What was NOT verified

- Not run against real Supabase/Redis/Stripe/third-party APIs — every
  test mocks at the boundary, matching this repo's established
  convention for this test tier.
- `core/lifespan.py` at 64.3% (up from 58.52%) is the one file in this
  sweep well short of the others' 85–100% range. This is intentional, not
  an oversight: the function individually try/excepts 17 separate
  background-loop imports+spawns, and exhaustively testing each loop's
  own import-success/import-failure branch would require either (a)
  spawning it for real under `ENV=test` (which the module's own
  extensive comment warns against — issue #2981) or (b) mocking all 17
  import targets individually, which wasn't done in this pass. The
  higher-value shared logic (`init_database`, `cleanup_database`, the
  `ENV=="test"` no-op guard, DB-init/Stripe-config/SGI-template-check
  gating) is covered; the per-loop import fan-out is not. A follow-up
  pass could close more of this gap without touching application code.
- The five findings listed in section 3 ("bugs found, not fixed") are
  exactly that — flagged for a human decision, not resolved here.
- No visual/manual verification was performed or applicable — this is a
  backend-only, test-only change with no UI surface.
