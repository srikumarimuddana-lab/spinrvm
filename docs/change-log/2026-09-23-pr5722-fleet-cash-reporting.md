# PR 5722: exclude refund holds from fleet cash payout analytics

Migration 454 updates the all-status admin payout statistics and the payout
window aggregations used by fleet reporting. Completed `clawback` adjustment
rows no longer inflate paid totals, completed transfer counts, payout volume,
settlement-time medians, daily cash totals, or top-driver cash totals. Other
payout rows remain included. Per-driver outstanding calculations continue to
deduct holds.

Alternatives considered: filtering all completed payouts would incorrectly
drop real transfers; removing holds from outstanding balances would make the
same earnings appear payable again. The migration scopes exclusion to cash
reporting functions only.

Blast radius is the admin `/payouts/stats` and `/payouts/overview` aggregate
RPC output. It is a forward-only function replacement with no table or row
changes. Dry-run scenarios: a completed clawback alone contributes $0 to cash
metrics; a completed standard or instant payout still counts; a clawback plus
cash payout reports only the cash payout's amount and count.

Rollback: re-run the original function definitions from migrations 162 and
384. No backfill is needed. Validation uses migration contract tests plus the
mocked route regression suites; direct live-database migration execution was
not performed.
