-- 292_financial_event_entries_created_at_index.sql
--
-- Rollback: DROP INDEX CONCURRENTLY IF EXISTS financial_event_entries_created_at;
--
-- Rollback plan (no deploy needed):
--   DROP INDEX CONCURRENTLY IF EXISTS financial_event_entries_created_at;
--   Nothing depends on it for correctness — migration 293's function returns
--   the same rows without it, just by scanning the whole table.
--
-- Supporting index for the daily "are any double-entry journals unbalanced?"
-- check (utils/reconciliation.py::_check_entry_balance), scoped by migration
-- 293's financial_event_entries_unbalanced_between().
--
-- The problem it fixes: that check asks "unbalanced entries for THIS day". It
-- did so by filtering the financial_event_entries_unbalanced view (migration
-- 286) on created_at — but that column is MIN(created_at), an AGGREGATE
-- OUTPUT, not a grouping key. Postgres cannot push a predicate on an aggregate
-- result below the GROUP BY, so every nightly run computed the full
-- GROUP BY event_id + HAVING over the ENTIRE table and only then discarded
-- everything outside the day. At the projected volume (~19k events/day at
-- ~3 legs each) that is ~20M rows within a year, re-aggregated every night to
-- return the zero rows it is expected to return.
--
-- Migration 293 moves the date predicate to where it can be pushed down. This
-- index is what makes that predicate cheap: financial_event_entries' existing
-- indexes are the UNIQUE (event_id, account, side) constraint and
-- (account, created_at DESC). Neither can serve a bare created_at range —
-- the composite has account as its leading column and Postgres has no index
-- skip scan — so without this the "legs written in this window" lookup is a
-- sequential scan and the fix buys nothing.
--
-- Cost: one B-tree entry per leg. financial_event_entries is written only by
-- the ledger_projection background loop (single-writer invariant), never in
-- the settlement request path, so this adds nothing to the P95 fare-settlement
-- SLA.
--
-- CONCURRENTLY because the table is populated in any environment where the
-- double-entry flag has been on; scripts/migrate.py detects CONCURRENTLY and
-- runs the file outside a transaction block, as Postgres requires.
--
-- NOTE: not required for correctness — the check returns the same answer
-- without it, only slower. Apply it before 293 so the new query path is never
-- the slow one.
--
-- Forward-compatible: new index only. No table altered, no backfill.

CREATE INDEX CONCURRENTLY IF NOT EXISTS financial_event_entries_created_at
    ON financial_event_entries (created_at);

COMMENT ON INDEX financial_event_entries_created_at IS
    'Serves financial_event_entries_unbalanced_between (migration 293): bounds '
    'the nightly trial-balance check to one day''s legs instead of aggregating '
    'the whole table. Not usable from the (account, created_at DESC) index, '
    'whose leading column is account.';
