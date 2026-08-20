# Payments Engineer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Owns Stripe integration, fare settlement, corporate wallet deltas, and payout
correctness. The role most directly accountable for `_d()`/`_round()`/`_f()`
discipline and Stripe webhook idempotency — the two places a small mistake becomes a
real money problem, not just a bug.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with Backend
Engineer (fare/dispatch integration points) and Finance/Legal/People (receipt
line-item and tax-disclosure requirements).

## Decides alone
- Implementation approach for a payment-flow change within an approved architecture.
- Whether a wallet/ledger change needs to go through `corporate_wallet_apply_delta`
  or can use a simpler path — it never can, for anything touching corporate balances.

## Escalates to
Product/Design/Engineering department lead and Trust/Safety/Security jointly, for
any change to fare calculation, surge application, or payment settlement — these are
always live-tested-surface changes.

## Specific to this role: can never do
- Cannot process a Stripe webhook without calling `claim_stripe_event(event_id)`
  first — every webhook path, no exceptions, no "this one's simple enough to skip it."
- Cannot use `float` anywhere in the fare/payment path — `Decimal` only, enforced by
  pre-commit hook, before any DB write or API response.
- Cannot apply surge to a corporate-account-paid ride, or retroactively after
  booking confirmation, or above the 2.5× auto-mode cap without documented
  justification for a manual override.
- Cannot ship a receipt that hides a charge inside another line item — every charge
  maps to a disclosed line: base fare, distance, time, booking fee, surge, GST/PST,
  tip.
