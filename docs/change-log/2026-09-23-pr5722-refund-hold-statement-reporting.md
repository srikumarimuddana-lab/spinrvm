# PR 5722: refund holds are not cash payouts in statements

Completed `clawback` payout rows remain visible in statement history as
“Refund hold adjustment,” but are excluded from paid-out totals. The hold still
reduces the available balance; this change only corrects cash-paid reporting.

Alternatives considered: hide adjustment rows (less transparent), or continue
counting them as paid (misstates cash delivered). Keeping a labeled row while
excluding its amount from cash totals preserves both audit visibility and the
meaning of “paid out.”

Blast radius is limited to the shared statement builder and its statement
consumers (driver, admin, and tax statement output). It does not change payout
creation, Stripe transfers, driver balance deductions, or prior-app payout
classification.

Dry-run scenarios: a period with only a completed clawback should show a $0.00
paid-out total and one visible adjustment row; a period with a completed
standard or instant payout plus a clawback should total only the actual cash
payouts; reversed/failed rows should continue to be excluded.

Rollback: revert this code change; no data migration or persisted data changes
are involved.

Validation: `pytest backend/tests/test_driver_statement.py` (targeted). No live
database or Stripe calls are required. Historical PDFs are not rewritten.
