# PR 5722: separate refund holds in admin payout reporting

The admin driver payout summary now excludes completed `clawback` adjustment
rows from `total_paid_out`, exposes them as `refund_holds_total`, and does not
present the latest clawback as the driver's “last payout.” The hold remains
deducted from `pending_balance`.

Alternatives considered: counting the hold as paid misstates delivered cash;
dropping the adjustment from balance math reopens funds for withdrawal. The
separate summary field retains both correct cash reporting and the hold.

Blast radius is limited to the admin driver payout summary response. No
historical payout rows or disbursements change. Dry-run case: $30 earnings,
$20 completed cash payout, and a $5 completed refund hold should report $20
paid, $5 held, and $5 pending balance.

Rollback: revert this code change; it has no persisted data or schema effects.
Validation: a targeted mocked regression was added to
`test_admin_drivers_coverage.py`. Pytest could not run because pytest is absent
from this environment. No live DB or Stripe calls were made.
