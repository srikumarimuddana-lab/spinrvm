-- 251_drivers_period1_accum_pending_index.sql
-- Partial index backing the Period-1 finalizer's pending-accumulator scan.
--
-- period1_distance_finalizer._pending_accumulators() runs, every 5 minutes when
-- period1_distance_tracking_enabled is on:
--     SELECT ... FROM drivers WHERE period1_accum_km > 0 LIMIT 500
-- period1_accum_km is 0 for all but the small in-flight set of drivers currently
-- mid-deadhead, so a PARTIAL index keeps this a tiny lookup instead of a full
-- sequential scan of the hot drivers table (which would worsen as the fleet
-- grows). The finalizer skips the scan entirely while the flag is off, so this
-- is the index to have in place before that flag is flipped on in production.
--
-- CREATE INDEX CONCURRENTLY: cannot run inside a transaction; the migration
-- runner auto-detects CONCURRENTLY and switches to autocommit (see migration 239).
--
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS drivers_period1_accum_pending;

CREATE INDEX CONCURRENTLY IF NOT EXISTS drivers_period1_accum_pending
    ON drivers (id)
    WHERE period1_accum_km > 0;
