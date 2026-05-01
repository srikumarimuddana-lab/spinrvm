-- Migration 54a: add assigned_at column to rides
--
-- PROBLEM: 54_dispatch_round_robin_index.sql creates an index on rides.assigned_at
-- but the column was never added to the table, causing migration 54 to fail and
-- blocking all subsequent migrations.
--
-- ROLLBACK PLAN (manual):
--   ALTER TABLE rides DROP COLUMN IF EXISTS assigned_at;
--
-- FORWARD COMPATIBILITY:
--   Column is nullable with no default — existing rows get NULL, which is correct
--   (no assigned_at before this migration ran). dispatch_service.last_assigned_driver_id()
--   handles NULL gracefully (returns None on empty result set).

BEGIN;

ALTER TABLE rides ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ;

-- Backfill: driver_notified_at is set at the same moment as driver assignment,
-- so it's the best available proxy for existing rows.
UPDATE rides
SET assigned_at = driver_notified_at
WHERE assigned_at IS NULL AND driver_notified_at IS NOT NULL;

COMMIT;
