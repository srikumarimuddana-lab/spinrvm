-- 85_drivers_add_is_suspended.sql
-- Migrations 77/80 created match_and_claim_driver() referencing d.is_suspended,
-- but the column was never added to the drivers table. Every dispatch call to
-- the function fails with: column d.is_suspended does not exist (42703).
--
-- Rollback: ALTER TABLE drivers DROP COLUMN IF EXISTS is_suspended;

ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_drivers_is_suspended
    ON drivers (is_suspended)
    WHERE is_suspended = true;
