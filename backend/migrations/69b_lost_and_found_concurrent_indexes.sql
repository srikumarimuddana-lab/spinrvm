/* 69b_lost_and_found_concurrent_indexes.sql
 * CONCURRENTLY-built indexes for the columns added in migration 69a.
 * Kept separate from 69a because CONCURRENTLY cannot run inside a transaction
 * and the runner (backend/scripts/migrate.py) uses a per-statement autocommit
 * path for any file that contains CONCURRENTLY.  In that path the runner skips
 * chunks whose stripped text starts with "--", so this file uses a block
 * comment header (no "--" prefix) and puts NO comment before any CREATE INDEX
 * statement so the first chunk always begins with the SQL keyword.
 *
 * Rollback (no semicolons here to avoid splitting this comment into fragments):
 *   DROP INDEX IF EXISTS lost_and_found_driver_id_idx
 *   DROP INDEX IF EXISTS lost_and_found_area_status_idx
 */
CREATE INDEX CONCURRENTLY IF NOT EXISTS lost_and_found_driver_id_idx
    ON lost_and_found (driver_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS lost_and_found_area_status_idx
    ON lost_and_found (service_area_id, status);

NOTIFY pgrst, 'reload schema';
