-- Rollback: ALTER TABLE drivers DROP COLUMN IF EXISTS location_updated_at;
--           DROP INDEX IF EXISTS idx_drivers_location_updated_at;

ALTER TABLE drivers ADD COLUMN IF NOT EXISTS location_updated_at TIMESTAMPTZ;

UPDATE drivers SET location_updated_at = updated_at WHERE location_updated_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_drivers_location_updated_at
    ON drivers(location_updated_at)
    WHERE is_online = true;
