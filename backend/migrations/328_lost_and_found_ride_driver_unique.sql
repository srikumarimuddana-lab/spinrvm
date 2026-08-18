-- 328_lost_and_found_ride_driver_unique.sql
-- Adds a unique constraint on (ride_id, driver_id) to prevent duplicate
-- lost_and_found cases for the same ride+driver pair.
--
-- Rollback: DROP INDEX CONCURRENTLY IF EXISTS lost_and_found_ride_driver_uniq;
--
-- Forward-compatible: CONCURRENTLY index creation, no lock on reads/writes.
-- Existing duplicates: checked — the application-level guard (migration N/A,
-- added in this PR) already prevents new duplicates; any historical duplicates
-- would cause this migration to fail. If that happens, de-duplicate first:
--   DELETE FROM lost_and_found a USING lost_and_found b
--   WHERE a.ride_id = b.ride_id AND a.driver_id = b.driver_id
--     AND a.created_at > b.created_at;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS lost_and_found_ride_driver_uniq
    ON lost_and_found (ride_id, driver_id)
    WHERE ride_id IS NOT NULL AND driver_id IS NOT NULL;
