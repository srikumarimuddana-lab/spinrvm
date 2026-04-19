-- P3-8: Add soft-delete support to core tables.
-- All three columns are nullable; NULL means the row is active.
-- Existing rows are unaffected (deleted_at remains NULL).

ALTER TABLE drivers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE users   ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE rides   ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Indexes on deleted_at speed up the IS NULL filter that every
-- live-row query now applies.
CREATE INDEX IF NOT EXISTS idx_drivers_deleted_at ON drivers (deleted_at);
CREATE INDEX IF NOT EXISTS idx_users_deleted_at   ON users   (deleted_at);
CREATE INDEX IF NOT EXISTS idx_rides_deleted_at   ON rides   (deleted_at);
