# PR 5722: exclude refund holds from fleet cash payout analytics

Migration 454 updates the all-status admin payout statistics and the payout
window aggregations used by fleet reporting, and replaces the period-close
snapshot from migration 391 with the same response shape. Completed `clawback`
adjustment rows no longer inflate paid totals, completed transfer counts,
payout volume, settlement-time medians, daily cash totals, top-driver cash
totals, or closed-period payout count, total, and first-50 audit IDs. Other
payout rows remain included. Per-driver outstanding calculations continue to
deduct holds. The payout-management list now types `payout_type` and labels
clawback rows as “Refund hold adjustment” alongside their ledger status.

Alternatives considered: filtering all completed payouts would incorrectly
drop real transfers; removing holds from outstanding balances would make the
same earnings appear payable again. The migration scopes exclusion to cash
reporting functions only.

Blast radius is the admin `/payouts/stats`, `/payouts/overview`, and period
close snapshot RPC output and the status cell for clawbacks in payout
management. It is a forward-only function replacement with no table or row
changes. Dry-run scenarios: a completed clawback alone contributes
$0 to cash metrics and period-close totals/IDs; a completed standard or instant
payout still counts; a clawback plus cash payout reports only the cash payout's
amount and count while preserving the snapshot's `payout_count`, `total_amount`,
and `payout_ids` keys and 50-ID cap.

Rollback: re-run the original function definitions from migrations 162, 384,
and 391. No backfill is needed. Validation uses migration contract tests and a
local PGlite PostgreSQL execution against synthetic payout rows; no live
database migration was executed. The migration contract suite was run locally;
the admin dashboard test runner was unavailable in this worktree.
