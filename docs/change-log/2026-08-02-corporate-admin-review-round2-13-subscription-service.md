# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "no pricing/fee mechanism exists for the corporate product" (business decision: flat SaaS subscription, full Stripe automation) — service-layer slice |

## 1. Issue / gap identified

Second slice of the corporate subscription-billing build (schema landed in
round2-12): no code path can actually start or stop a company's real
recurring Stripe charge yet.

## 2. Root cause

Never built (see round2-12 for the full background).

## 3. Fix / remediation

New `backend/services/corporate_subscription_service.py`:

- `assign_subscription(company_id, plan_id, admin_id)` — validates company
  + plan (active, has a `stripe_price_id`) exist, refuses if the company
  already has a live subscription (state machine enforced at both the
  service layer here and the DB partial-unique-index layer from round2-12),
  lazily creates the Stripe Customer if the KYB-time creation was somehow
  never completed (mirrors `routes/corporate_accounts.py`'s existing
  pattern exactly, including its deterministic idempotency key), requires
  a default payment method already on file (reuses
  `get_default_payment_method`, added for auto-topup), creates a real
  `stripe.Subscription`, persists the row with the plan's price **locked
  in at assignment time**, and writes an audit log entry.
- `cancel_subscription(company_id, admin_id, at_period_end=True)` —
  `at_period_end=True` (default) only flags `cancel_at_period_end` in
  Stripe and locally; the row's `status` stays `active` until the webhook
  handler (next commit) flips it when Stripe actually ends the period, so
  the company keeps access through what it already paid for.
  `at_period_end=False` cancels in Stripe immediately and flips the local
  row to `cancelled` right away rather than waiting for the webhook round
  trip.
- `list_plans()` — thin read-only wrapper.
- All money handling is `Decimal`, quantized to cents before persisting.
- Deliberately does **not** support an implicit plan-swap: switching plans
  is cancel-then-assign, two explicit admin actions, so an admin can never
  accidentally trigger an unintended Stripe proration.
- 15 unit tests added (`test_corporate_subscription_service.py`), covering
  the happy path, every precondition-rejection branch, the lazy-customer-
  creation path, and both cancellation modes.

## 4. Risk & impact on existing functionality

- **Blast radius: new file, new tests. No existing file touched.** Nothing
  yet calls this service (the admin route lands in a follow-up commit) —
  zero runtime behavior change to any currently-live code path.
- Reused, rather than duplicated, three existing patterns exactly:
  Stripe-customer lazy-create (`routes/corporate_accounts.py`),
  `get_default_payment_method` (`corporate_repo.py`, already used by
  auto-topup), and the `Decimal`/`asyncio.to_thread`/`stripe.error.StripeError`
  style from `corporate_wallet_winddown_service.py`.
- Grepped for any other consumer of `corporate_subscriptions`/
  `corporate_subscription_plans`: none — only this service and the
  round2-12 repo helpers reference them so far.
- Errors are never silently swallowed: expected precondition failures
  raise `CorporateSubscriptionError` (a `ValueError` subclass a route can
  map to a 4xx); a genuine `stripe.error.StripeError` propagates
  unmodified, per CLAUDE.md's "don't silently swallow payment errors."

## 5. User-experience effect

None yet — no route or UI wired to this service in this commit.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/corporate_subscription_service.py` | New file: assign/cancel/list | Service layer for flat SaaS corporate billing |
| `backend/tests/test_corporate_subscription_service.py` | New file: 15 unit tests | Cover every branch before the route exposes this to real admin traffic |

## 7. Rollback plan

`git revert` the commit. No migration, no data written by this commit —
purely new, uncalled code.

## 8. Verification performed

- [x] `ast.parse` syntax check on both new files — clean.
- [x] Manually traced every branch against its test: company-not-found,
      plan-not-found/inactive, plan-missing-stripe-price,
      already-has-a-live-subscription, no-payment-method, lazy customer
      creation, both cancellation modes, no-active-subscription-to-cancel.
- [x] Confirmed the idempotency-key choice (`corp-sub-create-{company_id}`,
      deterministic per company, not per call) matches the existing
      `cus-create-corp-{id}` / `corp-close-refund-{wallet_id}-{topup_id}`
      convention already in this codebase, rather than inventing a new
      scheme.
- [x] Did **not** run `pytest` for this file — per this round's explicit
      "don't run tests until everything is developed" instruction;
      deferred to the single end-of-round pass.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — nothing calls this yet
- [x] No behavior change to a working flow — new, uncalled code

## What was NOT verified

Did not run these tests, and did not exercise this service against real
(even sandbox) Stripe — no live Stripe test-mode calls are possible in
this session. The Stripe API surface used (`Customer.create`,
`Subscription.create/modify/delete`, `PaymentMethod.list` via the existing
`get_default_payment_method`) is reasoned from the same stripe-python
version's usage already proven working elsewhere in this codebase, not
independently confirmed. Full correctness — including whether Stripe's
actual `Subscription` object shape matches what `_period_end_iso` expects
on the live API version this account uses — will only be confirmed in
staging with a real Stripe test-mode account before this reaches a real
admin action (the follow-up route commit stays behind the
`corporate_subscription_billing_enabled` flag, default `false`, for
exactly this reason).
