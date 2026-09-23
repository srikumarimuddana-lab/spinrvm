# PR 5722: qualify refund holds only after a cash payout

The rollout flag is nullable with `DEFAULT false`; `NULL` is treated as off by
the RPC. This avoids a table-wide not-null constraint while retaining safe
default-off behavior. The atomic refund-hold predicate recognizes completed
`auto`, `instant`, and `standard` payouts with positive amounts. It excludes
`clawback` adjustments, payouts dated before the ride completed, and future
dated payout rows. Existing cumulative caps and replay protection remain in
place.

Alternatives considered: checking only `auto` misses cash already sent through
the instant/manual standard flows; treating every completed payout as cash
would let a refund hold trigger itself on replay. A known cash-payout type
allowlist keeps attribution tied to actual disbursement rows.

Blast radius: the predicate controls whether a subsequent Stripe refund can
create a capped driver clawback, within the existing refund transaction. It
does not alter payout rows or execute any backfill. The rollout flag remains
default-off until the existing rollout conditions are met.

Dry-run scenarios: an instant or standard completed cash payout after ride
completion should qualify; an older or future-dated payout, failed payout, or
completed clawback alone should not. Replay still must not add a second hold.

Rollback: because this migration is still unmerged and verified absent from
live `schema_migrations`, revert the migration edit before deployment if
needed. After deployment, set `settings.driver_refund_holds_enabled` to false
(or leave it null); do not delete attributed hold rows or restore the former
event writer.

Validation: PostgreSQL direct-pool regressions cover the positive and negative
eligibility cases. They require the direct-pool test database and were not run
in this environment. A local PGlite execution of migrations 451 and 454 also
verified that the nullable false-default flag supports enabled/disabled paths,
atomic rollback and retry, and that a closed-period snapshot returns only cash
payout IDs/count/amount. No live database writes were made.
