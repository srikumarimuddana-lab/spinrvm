-- 147_drivers_stale_intent_index.sql
--
-- Index for the stale-intent reconciler's candidate scan
-- (utils/stale_intent_reconciler.py):
--
--   SELECT ... FROM drivers WHERE is_online = true AND updated_at < cutoff
--
-- Runs every 15 minutes on every backend process. Without an index this is
-- a sequential scan over drivers; a partial index over the online subset
-- keeps it O(stale rows) and stays small because offline drivers (the vast
-- majority at any time) are excluded.
--
-- Flagged by review on PR #1848 — the reconciler and migration 146 shipped
-- the query pattern without the index, violating the index-with-query-pattern
-- migration rule.
--
-- CONCURRENTLY because drivers is a hot table (every location batch and
-- online/offline toggle writes to it); a plain CREATE INDEX would hold a
-- write-blocking lock for the duration of the build. The migration runner
-- detects CONCURRENTLY and switches to autocommit (scripts/migrate.py).
--
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_online_updated_at;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_online_updated_at
    ON public.drivers (updated_at)
    WHERE is_online = true;

COMMENT ON INDEX public.idx_drivers_online_updated_at IS
    'Partial index for the stale-intent reconciler scan (is_online = true AND updated_at < cutoff). Small by construction: only online drivers are indexed.';
