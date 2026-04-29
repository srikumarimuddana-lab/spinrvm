-- Rollback: ALTER TABLE rides DROP COLUMN quiet_mode; ALTER TABLE rides DROP COLUMN rider_notes;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS quiet_mode BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS rider_notes TEXT;
-- No RLS needed: rides table already has RLS; these are plain data columns.
-- No index needed: not used in WHERE clauses.
