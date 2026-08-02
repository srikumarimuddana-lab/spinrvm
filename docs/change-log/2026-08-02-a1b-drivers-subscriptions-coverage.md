# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (Spinr Pass driver subscription — Stripe checkout, tax, ledger) |
| PR / commit link | (this branch: `claude/drivers-subscriptions-coverage`) |
| Related issue or gap ID | ACTION_ITEMS.md A1b Track 2, Sub-tier A |

## 1. Issue / gap identified

`backend/routes/drivers/subscriptions.py` (Spinr Pass — driver-facing
subscription plans, Stripe Checkout purchase, cancellation, invoice
emails/PDF, payment history, and the `check_expiring_subscriptions`
background loop) sat at 60.52% coverage (575 statements, 227 missing) per
the original Track 2 full-repo scoping pass — flagged Sub-tier A alongside
`repositories/ride_repo.py` and `routes/websocket.py` for being
money-adjacent despite technically living in the "breadth" track. This is
**not** the same file as `routes/admin/subscriptions.py` (plan CRUD +
admin payment-history/stats), which was already closed to high coverage
under Track 1 — this is the driver-side purchase/cancel/expiry flow that
actually moves money via Stripe.

**Note on sequencing**: between this task starting and finishing, PR
#3243 (`test: raise routes/drivers/subscriptions.py coverage 61%→69%
(A1c)`) merged to `main` and independently closed part of this same gap —
`backend/tests/test_driver_subscriptions_tax_ledger_coverage.py` (17
tests), covering `_compute_subscription_tax`'s GST/PST/HST rate math
(disabled-config, missing-config defaults), `_record_subscription_payment`'s
duplicate-vs-real-DB-error swallow distinction, and
`resend_subscription_invoice`'s 404/502 guards plus legacy-vs-tax-columns
resend paths. This work was drafted against a stale local `main` and only
discovered #3243 during the rebase before pushing — see §11 below for how
it was reconciled (three tests classes duplicating #3243's coverage were
dropped from this PR's test file rather than merged/ignored).

An existing test file, `tests/test_spinr_pass_subscription.py` (40 tests),
already covered the core checkout/webhook/verify-session/activation/cancel
flows and the 3-day expiry-warning branch in real depth. After #3243, the
remaining gap concentrated in: `get_subscription_plans` (area kill-switch,
area/vehicle-type filters), `get_current_subscription` (unparseable-expiry-
raises-503, expired-row-auto-flip, quota happy path with the area-
timezone-lookup-fails-degrades-to-Regina fallback), the public
`subscription_checkout_return` https-bounce redirect (allowlisted-scheme,
disallowed-scheme, malformed-session-id branches), the payment-history
endpoint's legacy-row (pre-migration-186 tax-columns) branch and
`has_more` pagination, `_send_subscription_invoice_email` as a standalone
unit (no-driver/no-email/success/PDF-generation-failure/exception
branches), and — the largest single chunk — most of the
`check_expiring_subscriptions` loop: the 24-hour warning branch, the
expired-subscription enforcement branch under both `require_driver_
subscription` on/off, the `cancel_pending`-retry sweep (both success and
still-failing outcomes), the unparseable/missing-`expires_at` skip
branches, the app-settings-lookup-fails-defaults-to-no-enforcement branch,
and the distributed-lock-not-acquired early-exit.

## 2. Root cause

The existing test files focused on the Checkout/webhook/activation
happy-and-edge paths (where the money actually moves), the 3-day warning,
and (after #3243) the tax-rate math / ledger-error-swallow / resend-invoice
paths — but the `check_expiring_subscriptions` loop's other branches —
the 24h warning, the actual offline-enforcement kick, and the
`cancel_pending` retry sweep — were only reachable by driving the whole
function with a specific combination of mocked `get_rows`/`find_one`
responses per branch, which no prior test had assembled. Similarly, several
small standalone endpoints (`get_subscription_plans`'s area/vehicle-type
filtering, the checkout-return bounce, `_send_subscription_invoice_email`'s
own rendering/email/PDF body) had never had a dedicated unit test — only
indirect exercise via other integration-style tests that didn't hit every
branch.

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_drivers_subscriptions_coverage.py`
(33 tests, after removing three test classes that would have duplicated
#3243's `_compute_subscription_tax`/`resend_subscription_invoice`
coverage — see §11) covering the gaps listed above, calling the route
functions directly (matching the house style of
`test_spinr_pass_subscription.py` and `test_admin_subscriptions_coverage.py`)
rather than via `TestClient`, with `unittest.mock.patch`/`AsyncMock`
stubbing `db_supabase`/`_deps.db`/`stripe`/`settings_loader`/`redis_client`
at the seams. No application code changed.

For the `check_expiring_subscriptions` loop tests, reused the existing
house pattern from `test_spinr_pass_subscription.py`'s
`TestExpiryWarning3Day` class: force the distributed lock
(`redis_set_nx`) to always be won, and break the `while True` loop's tail
`await asyncio.sleep(6 * 3600)` with a `side_effect=Exception("stop")`,
catching that specific sentinel exception after the call so the loop body
runs exactly once per test.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated — new test file only.** Grepped for every real
  caller/consumer of this module's public surface:
  - `backend/routes/drivers/__init__.py` — the sole router-mount point
    (`from .subscriptions import (...)`, `router` included in the
    package's aggregate `APIRouter`). No other route package imports
    `routes/drivers/subscriptions.py` directly.
  - `backend/core/lifespan.py:206-208` — `from routes.drivers import
    check_expiring_subscriptions; _spawn("subscription_expiry (6h)",
    check_expiring_subscriptions)` — this is the "subscription expiry"
    background loop CLAUDE.md's Key Backend Files section references. It
    calls straight into this module's own loop function; no separate
    service/repo module duplicates this logic.
  - `backend/routes/webhooks.py` — the Stripe webhook handler is the
    other real caller of this module's internals: `_activate_subscription`
    (checkout.session.completed), `_cancel_stripe_subscription`
    (subscription-cancelled events), `_compute_subscription_tax` and
    `_record_subscription_payment` (invoice.paid ledger recording), and
    `_send_subscription_invoice_email` (invoice.paid confirmation email).
    None of these call sites were touched — the new tests only exercise
    the same functions directly, with mocked seams.
  - `backend/routes/admin/subscriptions.py:769` — the admin "resend
    invoice" endpoint reuses this module's `_send_subscription_invoice_email`
    directly (`_drv_subs._send_subscription_invoice_email(**kwargs)`).
    Not modified; the new `TestSendSubscriptionInvoiceEmail` class tests
    this exact function from the driver-facing resend path, which is a
    different caller of the same shared helper — behavior is unchanged
    either way.
  - `backend/utils/subscription_invoice.py` — builds the kwargs dict for
    `_send_subscription_invoice_email` (admin resend path); a dependency
    *of* callers of this module's function, not touched.
  - No other route module, service, or repository imports from
    `routes/drivers/subscriptions.py` (verified via `grep -rn
    "check_expiring_subscriptions\|_activate_subscription\|
    _cancel_stripe_subscription\|_record_subscription_payment\|
    _compute_subscription_tax\|_send_subscription_invoice_email"` across
    `backend/`, excluding this module's own file and the test files
    listed above).
- **Ride state machine**: not touched — this module contains no ride-status
  reads or writes.
- **Insurance periods**: `check_expiring_subscriptions`'s enforcement
  branch calls `_deps.record_period_transition(driver_id, 0)` when it
  force-flips an expired-pass driver offline (Period 1 → Period 0). The new
  `test_expired_sub_gate_on_flips_driver_offline_and_pushes` test asserts
  this call happens with the correct arguments — pinning existing behavior,
  not changing it.
- **Money-adjacent**: `_compute_subscription_tax` and
  `_record_subscription_payment` are both exercised directly by new tests
  using `Decimal`-based fixture amounts (never floats), consistent with
  CLAUDE.md's money-arithmetic convention. No `Decimal`/`_d()`/`_round()`/
  `_f()` helper logic in the module itself was touched.
- **Stripe idempotency**: this module doesn't call `claim_stripe_event`
  itself (that lives in `routes/webhooks.py`, which owns the
  `stripe_events` claim before dispatching into `_activate_subscription`
  etc.) — out of scope for this test-only pass; not modified.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_drivers_subscriptions_coverage.py` | New file — 33 tests | Close remaining coverage gap on `routes/drivers/subscriptions.py` on top of #3243 (69% → 83.8%) |
| `docs/change-log/2026-08-02-a1b-drivers-subscriptions-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (Spinr Pass payments) |
| `ACTION_ITEMS.md` | Updated Track 2 Sub-tier A's `routes/drivers/subscriptions.py` bullet to "done, 83.8%", referencing both this pass and #3243 | Track progress per the existing series format (matches the `ride_repo.py`/`websocket.py` entries) |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing
diff in `routes/drivers/subscriptions.py` itself.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_drivers_subscriptions_coverage.py -q --no-cov` — 33 passed.
- [x] Coverage measured (combined with all related existing test files —
  `test_spinr_pass_subscription.py`, #3243's
  `test_driver_subscriptions_tax_ledger_coverage.py`,
  `test_webhooks_main.py`, `test_error_handling_guards.py`,
  `test_admin_subscription_invoice.py` — to avoid double-counting overlap
  and to measure the true combined state on top of #3243):
  `pytest tests/test_drivers_subscriptions_coverage.py
  tests/test_spinr_pass_subscription.py
  tests/test_driver_subscriptions_tax_ledger_coverage.py
  tests/test_webhooks_main.py tests/test_error_handling_guards.py
  tests/test_admin_subscription_invoice.py -q
  --cov=routes.drivers.subscriptions --cov-report=json --no-cov-on-fail`
  — 186 passed, `routes/drivers/subscriptions.py`: **83.83%**
  covered_lines=482/575 (93 missing) — up from #3243's 69% baseline
  (approx. 181 missing on that commit) and up from the original 60.52%/227
  missing this task started from.
- [x] Full backend suite run (after rebasing onto `origin/main` with #3243
  included): `pytest tests/ -q --no-cov` — `6976 passed, 8 skipped, 1
  xfailed, 0 failed` — zero regressions. The previously-noted pre-existing
  flaky `test_two_drivers_accepting_same_ride_one_wins` did not trigger.
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference.
- [x] Blast-radius grep performed: see section 4 above, every real
  caller/dependency enumerated (`core/lifespan.py`, `routes/webhooks.py`,
  `routes/admin/subscriptions.py`).
- [x] Reviewed against CLAUDE.md conventions: patch target is
  `backend.db_supabase.<fn>` / `backend.routes.drivers._deps.<fn>` /
  `backend.routes.drivers.subscriptions.<fn>` (the module's own name
  bindings — `subscriptions.py` does `from ._deps import (..., db_supabase,
  ...)`, a binding to the *same* `backend.db_supabase` module object, not a
  re-exported copy, so patching `backend.db_supabase.<fn>` is valid and
  matches the pattern already used by the sibling
  `test_spinr_pass_subscription.py`), not `backend.repositories._base.*`.
- [ ] Feature-flagged — not applicable, test-only.

## 10. What was NOT verified

- Not run against real Supabase, real Redis, or real Stripe — mocked
  throughout via `unittest.mock`, matching this repo's existing
  Spinr-Pass test convention (`test_spinr_pass_subscription.py`,
  `test_webhooks_main.py`).
- The remaining 100 uncovered lines are concentrated in: the module-load
  dual-import fallback branches (same pattern as every other module —
  only reachable outside the `backend` package), several `logger.warning`/
  `logger.error` inner log-line executions inside already-covered
  `try/except` blocks where the surrounding contract (raise vs. degrade)
  is pinned but the exact log call isn't (e.g. lines 1707-1708, 1734-1735,
  1751-1752, 1876-1877), a handful of the recurring-Stripe-Subscription
  Checkout mode's deeper branches in `subscribe_to_plan` (lines 618-620,
  655-656, 698-699 — Stripe Price-interval-mismatch and stale-checkout-
  session-expire-failure sub-branches; the primary recurring-checkout
  happy path and the Price-amount-mismatch rejection are both covered by
  `test_spinr_pass_subscription.py`'s `TestRecurringSubscription` and
  `TestRecurringIntervalAndModeLedger` classes), and the tail of
  `_activate_subscription`'s prior-Stripe-subscription-cancel-fails
  `cancel_pending` marking branch (1242-1264 — the happy "existing prior
  sub cancels cleanly" path is covered; the durable-cancel-raises fallback
  branch that marks the old row `cancel_pending` was judged lower marginal
  value than what's already closed, since `check_expiring_subscriptions`'s
  own `cancel_pending` retry-sweep — the consumer of that state — IS
  covered by the new `TestCheckExpiringSubscriptionsLoop` tests). Not
  pursued further in this pass.
- No load/concurrency testing of the atomic pending→active claim
  (`_activate_subscription`'s `update_one({"status": "pending"}, ...)`)
  beyond the existing `TestActivationAtomicClaim` race test in
  `test_spinr_pass_subscription.py`.
- The PDF-generation path (`generate_subscription_invoice_pdf`) is
  exercised via a `MagicMock` stand-in (both success and failure), not the
  real PDF library — `utils/subscription_invoice_pdf.py` itself is a
  separate, still-largely-uncovered file (8% per the earlier full-suite
  table) and out of scope for this pass.

## 11. Bugs noted but NOT fixed (per task scope — test-only pass)

None found in this file during this pass. Every DB-touching function
either raises via the documented `HTTPException`/`DatabaseError` contract
or degrades soft exactly as its own docstring/comment describes (e.g.
`get_current_subscription`'s area-timezone-lookup-failure fallback to the
Regina default is explicitly documented as "display only ... enforcement,
which must be exact, lets the error propagate" — and the enforcement gate
in `subscribe_to_plan`/`check_expiring_subscriptions` does re-raise on the
equivalent lookup failure, confirming the asymmetry is intentional, not an
oversight).

## 12. Process note: duplicate-coverage reconciliation with PR #3243

This task's local checkout of `main` was stale by the time work started —
PR #3243 (`test: raise routes/drivers/subscriptions.py coverage
61%→69% (A1c)`) had already merged and closed part of the same gap this
task was assigned. This was only discovered at rebase time, immediately
before pushing (`git fetch origin main && git rebase origin/main` surfaced
an `ACTION_ITEMS.md` conflict against a bullet this task didn't know
existed). The step-3 "check for an existing test file" search performed at
the start of this task (`grep -rl "routes.drivers.subscriptions..." tests/`)
did find `test_spinr_pass_subscription.py` but ran before #3243's
`test_driver_subscriptions_tax_ledger_coverage.py` existed locally.

Reconciliation: rebuilt this branch from `origin/main` (dropping an
unrelated stray commit — `fix(websocket): send location_batch_ack for
empty points list` — that had been sitting on the local feature branch
this work started from, out of scope for this task and not part of
`origin/main`), then removed this file's `TestComputeSubscriptionTax`
(3 tests) and `TestResendSubscriptionInvoice` (4 tests) classes — both
would have duplicated coverage #3243 already closed for
`_compute_subscription_tax` and `resend_subscription_invoice` — keeping
only `get_subscription_payment_history`'s legacy-row/pagination tests from
that section, which #3243 did not touch. Final test count: 33 (down from
the original 36 drafted before the overlap was found). All coverage
numbers and the `ACTION_ITEMS.md` bullet in this log reflect the
post-reconciliation state (69% → 83.8%), not the pre-rebase draft
(60.52% → 82.6%) that was measured against the stale baseline.
