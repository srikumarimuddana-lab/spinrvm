-- 105_lost_and_found_add_missing_columns.sql
-- Adds columns and status values required by the lost-and-found route.
--
-- Root cause: the lost_and_found table existed in production before migration 69
-- was authored.  Migration 69 used CREATE TABLE IF NOT EXISTS, so it silently
-- skipped the entire table definition (including contact_method and the status
-- CHECK) on prod.  The four columns below were never in migration 69 at all.
-- This migration adds everything via ALTER TABLE so it is safe whether or not
-- migration 69 ran.
--
-- Rollback:
--   -- Scrub any rows that reference the widened status value first, otherwise
--   -- the constraint re-add below will immediately fail with a check violation.
--   UPDATE lost_and_found SET status = 'reported' WHERE status = 'driver_notified';
--   ALTER TABLE lost_and_found DROP CONSTRAINT IF EXISTS lost_and_found_status_check;
--   ALTER TABLE lost_and_found
--       ADD CONSTRAINT lost_and_found_status_check
--           CHECK (status IN ('reported', 'found', 'returned', 'unclaimed'));
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS contact_method;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS driver_id;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS notified_at;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS admin_notes;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS service_area_id;
--
-- Forward-compatible: ADD COLUMN IF NOT EXISTS; no data migration needed.
-- Lock note: DROP/ADD CONSTRAINT takes ACCESS EXCLUSIVE momentarily; lost_and_found
-- is a low-write table (one row per completed trip) so the window is negligible.
-- Index builds use CONCURRENTLY to avoid blocking writes during the scan.

ALTER TABLE lost_and_found
    ADD COLUMN IF NOT EXISTS contact_method  TEXT,
    ADD COLUMN IF NOT EXISTS driver_id       UUID REFERENCES drivers(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS notified_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS admin_notes     TEXT,
    ADD COLUMN IF NOT EXISTS service_area_id UUID REFERENCES service_areas(id) ON DELETE SET NULL;

-- Widen the status CHECK to include 'driver_notified'.
-- The inline CHECK from migration 69 is auto-named lost_and_found_status_check.
ALTER TABLE lost_and_found DROP CONSTRAINT IF EXISTS lost_and_found_status_check;
ALTER TABLE lost_and_found
    ADD CONSTRAINT lost_and_found_status_check
        CHECK (status IN ('reported', 'driver_notified', 'found', 'returned', 'unclaimed'));

-- Indexes: CONCURRENTLY avoids a write-blocking SHARE lock during the index build.
-- The migration runner (scripts/migrate.py) detects CONCURRENTLY and switches to
-- autocommit mode for this file automatically.
CREATE INDEX CONCURRENTLY IF NOT EXISTS lost_and_found_driver_id_idx
    ON lost_and_found (driver_id);

-- Compound index covers the admin list endpoint's combined status + service_area_id filter.
CREATE INDEX CONCURRENTLY IF NOT EXISTS lost_and_found_area_status_idx
    ON lost_and_found (service_area_id, status);

NOTIFY pgrst, 'reload schema';
