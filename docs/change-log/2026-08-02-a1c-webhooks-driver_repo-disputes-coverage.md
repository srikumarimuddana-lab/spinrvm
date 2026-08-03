# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, dispatch, admin |
| PR / commit link | (this branch: `claude/a1c-subtier-c-batch-2`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier C (`routes/webhooks.py`, `repositories/driver_repo.py`, `routes/disputes.py`) |

## 1. Issue / gap identified

Three named Sub-tier C files remained open:
- `backend/routes/webhooks.py` (75.40%, 748 stmts) — Stripe/SES/Twilio inbound webhook handlers.
- `backend/repositories/driver_repo.py` (72.99%, 137 stmts) — driver lookups, location, availability, atomic claims.
- `backend/routes/disputes.py` (73.88%, 134 stmts) — payment dispute/refund endpoints.

## 2. Root cause

**`webhooks.py`**: the massive `stripe_webhook` route (~1150 lines) already
had extensive coverage from `test_webhooks_main.py`/`test_orphan_refund.py`/
`test_webhook_stripe_v15.py`, but none of the module-private helper
functions supporting the SES and Twilio inbound webhooks had a *direct*
unit test anywhere (`_extract_invoice_payment_intent`,
`_invoice_period_end_iso`/`_invoice_period_start_iso`,
`_confirm_sns_subscription`, `_topic_arn_allowed`, `_suppress_address`,
`_suppress_marketing_email`, `_handle_ses_notification`,
`_resolve_user_id_by_phone`, `_handle_sms_keyword`) — confirmed via
`grep -rln "<helper_name>" backend/tests/*.py` returning nothing for each,
before this batch. They were only reachable indirectly through
`test_ses_webhook.py`/`test_twilio_inbound.py`'s signature-verified HTTP
round-trips, which don't reach every branch (e.g. the
`Invoice.retrieve` Basil-fallback exception path, the untrusted-SubscribeURL
refusal, the per-channel bounce/complaint suppression split).

**`driver_repo.py`**: only `set_driver_available(available=True)` had a
direct unit test (`test_set_driver_available_invariant.py`); every other
function (caching branches on `get_driver_by_id`/
`get_driver_by_user_id_cached`, `get_service_area_for_point`'s RPC
exception path, `update_driver_location`'s heading normalization,
`set_driver_available(available=False)`'s release path,
`match_and_claim_driver`/`claim_driver_atomic`/`claim_ride_atomic`'s
claim-won-vs-claim-lost branches, `update_acceptance_rate`'s EWMA math and
swallow-on-exception, `get_driver_status_by_user`'s default/not-found
branches) was exercised only indirectly through higher-level route/service
tests that mock at a different layer.

**`disputes.py`**: the user-facing endpoints (`create_dispute`/
`get_user_disputes`/`get_dispute`) were well covered by
`test_p3_addresses_favorites_safety_disputes.py`, and
`admin_resolve_dispute`'s Stripe-refund happy path (HALF_UP cents
conversion, idempotency key) was covered by `test_dispute_refund_cents.py`.
Uncovered: `admin_get_disputes` (zero direct test — enrichment with
users/rides, "Unknown" fallback, status filter) and most of
`admin_resolve_dispute`'s guard/error branches (404 not found, 400
already-resolved, 400 refund-exceeds-fare, `manual_required` when no
`payment_intent_id`, 503 Stripe-not-configured, 502 Stripe-exception,
`rejected` resolution, `approved` with no `refund_amount`, and the
push-notification-failure swallow).

Separately, both `routes/webhooks.py`... *(N/A, webhooks has no dead
admin_router)* — but **`routes/disputes.py`'s `admin_router`
(`admin_get_disputes`/`admin_resolve_dispute`) is dead code**, same pattern
as `routes/promotions.py`'s in the prior batch: never mounted in
`backend/server.py` (only this module's user-facing `api_router` is; the
live `/api/admin/disputes` surface is `routes/admin/support.py`, already
covered by `test_admin_support_routes.py`). Confirmed via
`grep -n "disputes" backend/server.py`. Both functions were exercised here
as plain async functions (matching how `test_dispute_refund_cents.py`
already tests `admin_resolve_dispute`), not via HTTP, for coverage
purposes — flagged, not fixed.

## 3. Fix / remediation

Test-only change, three new files:
- `backend/tests/test_webhooks_helpers_coverage.py` (39 tests) — the nine
  SES/Twilio/invoice helper functions listed above.
- `backend/tests/test_driver_repo_coverage.py` (38 tests) — every function
  in the module.
- `backend/tests/test_disputes_admin_coverage.py` (13 tests) —
  `admin_get_disputes` + `admin_resolve_dispute`'s remaining branches.

No application code changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Three new test files only; zero application
  code touched.
- **`webhooks.py`**: other consumers of the tested private helpers are none
  — they're all module-private and called only from within
  `routes/webhooks.py`'s own route handlers (`ses_sns_webhook`,
  `twilio_inbound_sms`, `stripe_webhook`'s invoice.paid branch). New tests
  mock at the same `db_supabase`/`services.marketing_consent`/
  `utils.sns_verify`/`httpx.AsyncClient` seams the module already imports.
- **`driver_repo.py`** — per
  `grep -rln "driver_repo" backend --include=*.py | grep -v tests/`, this
  module is imported by `services/dispatch_service.py`,
  `routes/drivers/*.py`, `db_supabase.py` (re-export), and
  `utils/driver_claim_reaper.py`. None of these call sites are modified;
  the new tests patch `driver_repo.supabase`/`driver_repo.run_sync`/
  `driver_repo.invalidate_driver_cache` directly (the module's own bound
  names, per CLAUDE.md's patch-target rule), so no other module's behavior
  is exercised differently than before. The `is_available ⇒ is_online`
  invariant (already pinned for `available=True` by
  `test_set_driver_available_invariant.py`) is now also pinned for the
  `available=False` release path and the `total_rides_inc`-triggered read —
  confirming, not changing, the documented invariant.
- **`disputes.py`** — dead-code finding: `admin_router`'s two functions are
  unreachable via HTTP. Not removed in this PR; flagged for a separate
  cleanup decision (same caveat as the `promotions.py` finding — confirm no
  dynamic re-mount exists before deleting).
- **Money-adjacent (`disputes.py`, `webhooks.py`)**: every new Stripe-refund
  and invoice-payment-intent test mocks `stripe.Refund.create` /
  `stripe.Invoice.retrieve` at the same seam existing tests use; no test
  performs a real Stripe call. Decimal-only math preserved throughout.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_webhooks_helpers_coverage.py` | New file — 39 tests | Close coverage gap on `routes/webhooks.py` (75.40% → 78%) |
| `backend/tests/test_driver_repo_coverage.py` | New file — 38 tests | Close coverage gap on `repositories/driver_repo.py` (72.99% → 99%) |
| `backend/tests/test_disputes_admin_coverage.py` | New file — 13 tests | Close coverage gap on `routes/disputes.py` (73.88% → 94%) |
| `docs/change-log/2026-08-02-a1c-webhooks-driver_repo-disputes-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface |
| `ACTION_ITEMS.md` | Sub-tier C section | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_webhooks_helpers_coverage.py tests/test_driver_repo_coverage.py tests/test_disputes_admin_coverage.py -q --no-cov` — 90 passed (one initial failure in a `get_driver_status_by_user` test fixture — `{}` is falsy in Python so it hit the not-found branch instead of the missing-status-default branch — fixed in the test, not the source).
- [x] Coverage measured per file:
  - `routes/webhooks.py`: **75.40% → 78%** (748 stmts, 168 missing — the dual-import fallback block, and large remaining chunks of `stripe_webhook`'s deep event-type branches around lines 904-1094/1867-1901 that were out of scope for this batch's helper-function focus; a future pass should target those directly). 140 passed across all webhook-related test files run together, 0 failed.
  - `repositories/driver_repo.py`: **72.99% → 99%** (137 stmts, 2 missing — the dual-import `ImportError` fallback block, structurally unreachable in this harness, same documented pattern as prior files). 57 passed, 0 failed.
  - `routes/disputes.py`: **73.88% → 94%** (134 stmts, 8 missing — the dual-import fallback block and the notification-body's `resolution == "refund"` dead branch, which can never be true since `ResolveDisputeRequest.resolution` is only ever `approved`/`partial_refund`/`rejected` per the docstring — a latent-but-harmless dead conditional, not a bug worth fixing in a test-only PR). 48 passed, 0 failed.
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — `8546 passed, 8 skipped, 1 xfailed, 0 failed` (up from 8456 in the prior batch-1 checkpoint). No regressions.
- [x] Blast-radius greps performed for all three files (see §4 above).
- [x] Reviewed against CLAUDE.md conventions: confirmed Stripe idempotency (`claim_stripe_event`) and money-arithmetic (Decimal-only, `dollars_to_cents` HALF_UP) conventions are exercised, not altered, by the new tests.

## 10. What was NOT verified

- Not run against real Stripe/Supabase/SNS/Twilio — every external call is
  mocked, matching repo convention for this test tier.
- `webhooks.py`'s huge `stripe_webhook` route still has real remaining gaps
  (see §9) — this batch targeted the previously-zero-coverage private
  helpers, not an exhaustive pass on the route itself; flagging this as
  unfinished rather than implying the file is "done."
- The two dead `admin_router` functions in `disputes.py` were verified to
  work correctly as plain Python functions, but their behavior *as HTTP
  endpoints* was never verifiable in the first place since the router isn't
  mounted — inherent to the finding, not a testing gap in this PR.
- No visual/UI verification — backend-only, no frontend surface in this diff.
