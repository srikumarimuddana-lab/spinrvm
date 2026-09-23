# PR 5722: separate refund holds from cash paid in balance reporting

The driver balance still deducts completed `clawback` rows from
`payable_balance`, preserving the hold against further withdrawal. The
`total_paid_out` figure now excludes those rows and a new `refund_holds_total`
field exposes their amount separately.

Alternatives considered: retaining the hold inside `total_paid_out` conflates
cash delivered with an accounting adjustment; removing the deduction would
make held earnings withdrawable again. Separate reporting preserves the
available-balance control and the cash meaning of paid out.

Blast radius is the driver `/balance` response and downstream displays that
consume its aggregate payout fields. No payout state, balance arithmetic,
Stripe transfer, or prior-app accounting changes.

Dry-run scenarios: completed cash payout plus completed clawback reports cash
paid and held amounts separately while subtracting both from payable balance;
failed or reversed clawbacks remain excluded as money-out. Historical hold
rows require no rewrite.

Rollback: revert the application code. No persisted schema or data change is
involved. The new response field is additive.

Validation: targeted regression added to `test_earnings_coverage.py`. Test
execution was unavailable because this environment has no `pytest` module;
no live DB or Stripe calls were made.
