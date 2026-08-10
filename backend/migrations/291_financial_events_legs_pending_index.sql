-- 291_financial_events_legs_pending_index.sql
--
-- Rollback: DROP INDEX CONCURRENTLY IF EXISTS financial_events_legs_pending;
--
-- Supporting index for the double-entry projection work queue introduced in
-- migration 287 (financial_events_missing_legs).
--
-- The problem: that RPC asks for
--     WHERE NOT EXISTS (legs)
--       AND event_type IN ('stripe_charge','stripe_refund')
--       AND delta_cents <> 0
--       AND created_at < now() - interval '30 minutes'
--     ORDER BY created_at
--     LIMIT ...
--
-- financial_events' existing indexes (migration 58) are (user_id, created_at),
-- (event_type, created_at DESC) and (ride_id). The event_type index can serve
-- ONE event_type as an ordered range scan, but an IN over two values forces
-- either a bitmap combination — which loses created_at ordering and so needs a
-- full Sort before LIMIT can apply — or a full index scan with filter. Either
-- way Postgres must materialise and sort every qualifying row to answer
-- "oldest N", on every tick.
--
-- That is worst exactly when it matters most: during the initial backfill,
-- essentially the entire historical stripe_charge/stripe_refund population
-- qualifies, and the loop re-sorts that (shrinking but large) backlog every 15
-- minutes until it drains.
--
-- A partial index on created_at with the two static predicates baked in gives
-- the planner an ordered scan it can stop after LIMIT rows. It stays small
-- because it only covers rows still awaiting legs' worth of filtering — and it
-- costs nothing on the settlement write path beyond one small index entry.
--
-- CONCURRENTLY because financial_events is populated and on the settlement
-- path; scripts/migrate.py detects CONCURRENTLY and runs the file outside a
-- transaction block, as Postgres requires.
--
-- NOTE: this must be applied before ledger_double_entry_enabled is turned on
-- in any environment with meaningful ledger history. It is not required for
-- correctness — the RPC returns the same rows either way — only for the query
-- to stay cheap at scale.

CREATE INDEX CONCURRENTLY IF NOT EXISTS financial_events_legs_pending
    ON financial_events (created_at)
    WHERE event_type IN ('stripe_charge', 'stripe_refund')
      AND delta_cents <> 0;

COMMENT ON INDEX financial_events_legs_pending IS
    'Serves financial_events_missing_legs (migration 287): ordered oldest-first '
    'scan of projectable ledger headers so the LIMIT can short-circuit instead '
    'of sorting the whole backlog each 15-minute projection tick.';
