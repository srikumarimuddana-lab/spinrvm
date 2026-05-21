-- Migration 91: driver_location_history — add accuracy/altitude columns + ride_id FK
--
-- Why:
--   The single-point WS insert omits accuracy/altitude because the columns
--   were not in the original schema (migration 08). The batch-insert path
--   already sends them, so the columns should exist but we add them
--   defensively here.
--
--   Adding a FK on ride_id prevents orphaned breadcrumbs from accumulating
--   against deleted or never-existed ride rows. ON DELETE SET NULL means a
--   ride deletion scrubs the FK without deleting the GPS trail (needed for
--   regulatory audit). NOT VALID skips validation of existing rows so this
--   runs without a full table scan in production — safe to apply live.
--
-- Rollback plan (if needed before validate):
--   ALTER TABLE driver_location_history
--       DROP CONSTRAINT IF EXISTS driver_loc_hist_ride_fk;
--   ALTER TABLE driver_location_history
--       DROP COLUMN IF EXISTS accuracy,
--       DROP COLUMN IF EXISTS altitude;

-- accuracy: horizontal accuracy radius in metres (from GPS hardware / OS).
-- Null-safe: old rows and rows from clients that don't send it remain NULL.
ALTER TABLE driver_location_history
    ADD COLUMN IF NOT EXISTS accuracy  DOUBLE PRECISION DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS altitude  DOUBLE PRECISION DEFAULT NULL;

-- Soft FK: orphaned breadcrumbs (ride deleted mid-trip edge case) become
-- ride_id = NULL rather than causing a constraint violation.
-- NOT VALID = add constraint, skip full-table validation, safe for live traffic.
--
-- Postgres does not accept "ADD CONSTRAINT IF NOT EXISTS" — that syntax is
-- only valid for columns and indexes. Guard with a DO block that checks
-- pg_constraint so the migration is idempotent for re-runs.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'driver_loc_hist_ride_fk'
    ) THEN
        ALTER TABLE driver_location_history
            ADD CONSTRAINT driver_loc_hist_ride_fk
            FOREIGN KEY (ride_id)
            REFERENCES rides (id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END $$;
