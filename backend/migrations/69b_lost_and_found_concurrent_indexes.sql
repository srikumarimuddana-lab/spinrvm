-- 69b_lost_and_found_concurrent_indexes.sql
--
-- Adds the CONCURRENTLY-built indexes for the new columns introduced in 69a.
-- Kept separate from 69a because CONCURRENTLY cannot run inside a transaction
-- and the runner uses a different (autocommit, per-statement) path for any
-- file containing CONCURRENTLY.  In that path the runner skips statement
-- chunks whose stripped text starts with "--", so this file avoids leading
-- comment lines before any executable statement.
--
-- Rollback:
--   DROP INDEX IF EXISTS lost_and_found_driver_id_idx;
--   DROP INDEX IF EXISTS lost_and_found_area_status_idx;

CREATE INDEX CONCURRENTLY IF NOT EXISTS lost_and_found_driver_id_idx
    ON lost_and_found (driver_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS lost_and_found_area_status_idx
    ON lost_and_found (service_area_id, status);

NOTIFY pgrst, 'reload schema';
