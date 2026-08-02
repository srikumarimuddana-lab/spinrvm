# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch: `claude/a1c-subscriptions-coverage`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c Sub-tier A |

## 1. Issue / gap identified

`backend/routes/drivers/subscriptions.py` (Spinr Pass — driver subscription
plans, Stripe checkout, invoice emails, activation, payment history,
cancellation, and the `check_expiring_subscriptions` background loop) sat
at 61% coverage (575 statements, 227 missing). The gap concentrated in
four areas: the invoice-email mailer (`_send_subscription_invoice_email`,
~170 lines, essentially untested), `_activate_subscription`'s
degrade/failure branches, the `resend_subscription_invoice` endpoint
(entirely untested), and — the largest single gap —
`check_expiring_subscriptions` itself, one of the 17 startup loops
documented in root `CLAUDE.md`.

**Note on a concurrent session:** while this session was in progress, a
different same-day A1c session (PR #3243, merged as `a0888fd`) landed its
own partial pass on this exact file — `test_driver_subscriptions_tax_ledger_coverage.py`
(17 tests), raising coverage 61% → 69% by closing `_compute_subscription_tax`,
`_record_subscription_payment`, and part of `resend_subscription_invoice`.
This branch fast-forward-merged onto that commit (`git merge origin/main
--ff-only`, clean — this branch had not yet touched `ACTION_ITEMS.md`) before
finishing, so the work below picks up from that 69% starting point rather
than the original 61% baseline, with some resulting test overlap on the
tax/ledger/resend functions (see section 3).

## 2. Root cause

`test_spinr_pass_subscription.py` (15 test classes, pre-existing) covers
the checkout/webhook/verify-session happy paths thoroughly but was scoped
to that flow, not the invoice mailer, the expiry-enforcement loop, or the
driver-initiated resend endpoint. No prior session had written a dedicated
test file for those parts of the module.

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_subscriptions_coverage.py` (66
tests, in a new file rather than extending `test_spinr_pass_subscription.py`
— that file is already large and cohesive around the checkout/webhook/
verify-session flow; this one is scoped to the invoice mailer, activation
degrade branches, the resend endpoint, and the background loop, mirroring
how `test_ride_repo_coverage.py` was kept separate from
`test_ride_route_contract.py` earlier in this sprint). No application code
changed.

Coverage closed, by function:
- `_send_subscription_invoice_email` — driver/user-not-found short-circuits,
  full success path (PDF attachment generated, GST+PST+HST tax rows,
  Stripe-receipt link), PDF-generation failure degrading to HTML-only send,
  email-delivery failure, the `_pct_label` divide-by-zero guard, and the
  outer catch-all exception path.
- `_activate_subscription` — driver-lookup exception during activation
  (must not abort activation since the driver already paid), area-timezone
  lookup exception (falls back to Regina default expiry), and the
  prior-subscription Stripe-cancel-failure → `cancel_pending` branch (a
  plan-switch where the old Stripe subscription can't be cancelled must not
  block activating the new, already-paid-for, pass).
- `resend_subscription_invoice` — missing driver profile (404), payment not
  found / not owned (404), legacy pre-migration-186 rows (no stored tax
  columns), unparseable `created_at` falling back to `now()`, and
  delivery failure (502).
- `check_expiring_subscriptions` — the distributed-lock not-acquired branch
  (and its `continue` back to the top of the loop), the `cancel_pending`
  retry sweep (success / still-failing / query-exception), the
  `get_app_settings` failure defaulting `require_driver_subscription` to
  `False`, the mark-expired write failing, the full online-driver
  enforcement path (offline flip, insurance-period transition, presence
  clear, WS disconnect, activity-log insert, push, admin broadcast — each
  independently swallowing its own failure), the driver-already-offline
  skip, the 24h and 3-day warning branches (including push-failure
  swallow, missing/unparseable `expires_at`, naive-datetime normalization,
  and the lost-atomic-claim-race skip on the 3-day query).
- Assorted smaller gaps: `get_subscription_plans`'s area-disabled free-mode
  message and its area/vehicle-type filtering; `get_current_subscription`'s
  no-driver / no-active-sub / past-expiry-flip / unparseable-expiry /
  area-timezone-failure branches; `_cancel_stripe_subscription`'s two
  `raise_on_error=True` paths; `_compute_subscription_tax`'s full
  GST+PST+HST calc and its disabled/no-driver zero-tax defaults;
  `subscribe_to_plan`'s driver/plan-not-found, vehicle-type and
  service-area mismatch (with and without a covering parent area),
  area-timezone-failure fallback, client-supplied deep-link scheme,
  Stripe-Price-retrieve failure, a generic exception inside the checkout
  block, the stale-session and race-loser Stripe-session-expire failures
  being swallowed, and dev-mode cancelling a prior active subscription;
  `cancel_subscription`'s missing-driver/no-active-sub paths;
  `subscription_checkout_return`'s allowlisted-scheme, disallowed-scheme
  fallback, unsafe-session-id-stripped, and no-session-id branches;
  `get_subscription_payment_history`'s stored-tax-column (post-migration-186)
  serialization branch; and `_record_subscription_payment`'s
  duplicate-vs-real ledger-insert-failure branches plus the
  `stripe_invoice_url` column write.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to a new test file.** No application code in
  `routes/drivers/subscriptions.py` (or anywhere else) was modified.
  Grepped every real caller of the functions under test:
  - `check_expiring_subscriptions` — spawned exactly once, as a background
    task, from `backend/core/lifespan.py` (`_spawn("subscription_expiry (6h)",
    check_expiring_subscriptions)`). Not called from any request path.
  - `_activate_subscription` — called from `routes/webhooks.py` (the
    `checkout.session.completed` / `invoice.paid` Stripe webhook handlers)
    and from `verify_subscription_session` within this same module. Both
    call sites are unmodified.
  - `_send_subscription_invoice_email` — called from within this module
    (`_activate_subscription`, `resend_subscription_invoice`) **and** from
    `routes/admin/subscriptions.py`'s `admin_resend_subscription_invoice`
    (the admin-triggered, audit-logged, Redis-cooldown-gated resend
    endpoint). That admin endpoint is a real, distinct consumer of this
    function and was not touched — the new tests exercise the same
    function signature it calls.
  - `subscribe_to_plan`, `verify_subscription_session`,
    `get_subscription_plans`, `get_current_subscription`,
    `cancel_subscription`, `resend_subscription_invoice`,
    `get_subscription_payment_history`,
    `subscription_checkout_return` — all are FastAPI route handlers
    mounted directly on `router`; their only "callers" are the driver-app
    HTTP client and (for `checkout-return`) Stripe's own redirect. No other
    backend module calls them directly.
  - `_compute_subscription_tax`, `_cancel_stripe_subscription`,
    `_record_subscription_payment` — private helpers used only within this
    module (by the functions listed above); no external callers.
- **Money-adjacent, but test-only.** This module realizes revenue (Stripe
  Checkout, the `subscription_payments` ledger, invoice emails) and gates
  driver online/available state on subscription expiry — both squarely in
  the "payments" and "drivers" live-tested domains per CLAUDE.md. No
  Decimal/money-arithmetic code was changed; tests assert against `Decimal`
  values throughout (no float comparisons introduced).
- **Insurance-period adjacency**: `check_expiring_subscriptions`'
  enforcement branch calls `record_period_transition(driver_id, 0)` when it
  force-offlines a driver whose pass expired — this is the one place this
  module touches the Period 0-3 insurance state machine documented in root
  CLAUDE.md. The new tests assert this call fires on the happy path and
  does NOT fire when the driver-offline-flip write itself fails (enforcement
  aborts before the period transition, matching the code's existing
  ordering) — no change to that ordering was made, only test coverage of
  the existing behavior.
- **No production code touched** — nothing to regress in ride state,
  wallet/allowance deltas, or dispatch. The one background loop tested here
  (`check_expiring_subscriptions`) is explicitly documented in the task as
  "testing it, not modifying its replay-safety logic," and no changes were
  made to its Redis distributed-lock or idempotency-flag (`expiry_warned`,
  `expiry_warned_3d`) logic.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_subscriptions_coverage.py` | New file — 66 tests | Close coverage gap on `routes/drivers/subscriptions.py` (61% → 99%) |
| `docs/change-log/2026-08-02-a1c-subscriptions-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (payments) |
| `ACTION_ITEMS.md` | Updated A1c Sub-tier A's `routes/drivers/subscriptions.py` bullet | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] New test file run alone: `pytest tests/test_subscriptions_coverage.py -q --no-cov` — **66 passed**.
- [x] Run together with the other three files touching this module:
  `pytest tests/test_subscriptions_coverage.py tests/test_spinr_pass_subscription.py tests/test_webhooks_main.py tests/test_admin_subscriptions_coverage.py -q --cov=routes.drivers.subscriptions --cov-report=term-missing --no-cov-on-fail`
  — **198 passed**, no collisions.
- [x] Coverage measured (same command as above, matching how the 61%
  baseline was taken): **`routes/drivers/subscriptions.py`: 99%** (575
  statements, 6 missing — up from 61%/227 missing). The 6 remaining lines
  are the dual-import `ImportError` fallback for `redis_set_nx` when
  *neither* import form resolves (`utils/redis_client.py` genuinely exists
  in both package and top-level layouts in this repo, so this fallback is
  unreachable without monkeypatching `sys.modules` import machinery — not
  attempted, judged not worth the fragility) and the outermost
  `except Exception` / `loop_monitor` `ImportError` guard around the whole
  loop body (a defensive catch-all for a scenario — an exception escaping
  every inner try/except — that would require breaking one of the inner
  guards to reach).
- [x] Full backend suite: `pytest tests/ -q --no-cov` — **7134 passed, 8
  skipped, 1 xfailed, 0 failed** (323s). The recorded session-start
  baseline was 7000 passed; between then and this branch starting work,
  `origin/main` moved 4 commits ahead including a concurrent same-day A1c
  session on this exact file (PR #3243, `a0888fd` — see section 1 note
  below) that itself added 68 tests (`test_driver_subscriptions_tax_ledger_coverage.py`
  + `test_redis_client_coverage.py`), bringing the true pre-this-session
  full-suite count to 7068. This branch was fast-forward-merged onto that
  commit before finishing (`git merge origin/main --ff-only`, no conflicts
  — nothing in this branch had touched `ACTION_ITEMS.md` yet), so the
  final delta is 7068 → 7134 = **+66**, matching the number of new tests
  added here, zero regressions.
- [x] Blast-radius grep performed: see section 4 above, every real caller
  enumerated and confirmed unmodified.
- [x] Reviewed against CLAUDE.md conventions: patch targets follow this
  module's dual-binding pattern — `db_supabase.<fn>` (module reference,
  shared by `_deps.db.<fn>` too) vs. `_deps.<name>` (bound-name copies like
  `send_push_notification`/`manager`/`clear_presence`/
  `record_period_transition`) vs. the source module for functions
  re-imported inside a function body on every call
  (`utils.spinr_pass.area_timezone`, `utils.redis_client.redis_set_nx`) —
  matching the existing convention documented at the top of the new test
  file and used throughout `test_spinr_pass_subscription.py`.
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference.
- [ ] Feature-flagged — not applicable, test-only.

## 10. What was NOT verified

- Not run against real Supabase — mocked throughout, matching repo
  convention for this test tier.
- The `check_expiring_subscriptions` tests exercise exactly one iteration
  of the `while True:` loop per test (via forcing `asyncio.sleep` to raise
  a sentinel exception, the same technique already used by
  `TestExpiryWarning3Day` in `test_spinr_pass_subscription.py`) — the
  Redis distributed-lock's real cross-replica mutual-exclusion behavior
  under concurrent replicas is not exercised here (only that the code path
  correctly branches on the lock result); that would require a real or
  fake-Redis integration test, out of scope for this coverage pass.
- No bugs found or fixed in `routes/drivers/subscriptions.py` itself during
  this pass — this was a pure test-coverage exercise per the task
  instructions, and no behavior worth flagging as a "considered but not
  fixed" finding turned up (unlike the `mark_stripe_event_processed` swallow
  and `location_batch_ack` gap found in the two prior sibling passes today).
- The 6 remaining uncovered lines (see section 9) are both defensive
  fallback branches judged not worth chasing given the fragility of the
  monkeypatching required to reach them; not pursued further in this pass.
