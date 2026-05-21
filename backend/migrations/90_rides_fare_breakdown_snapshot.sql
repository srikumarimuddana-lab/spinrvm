-- Migration 90: Add fare_breakdown_snapshot to rides
-- Rollback: ALTER TABLE rides DROP COLUMN IF EXISTS fare_breakdown_snapshot;
--
-- Stores the exact fare breakdown shown to the rider at booking time.
-- When fare_lock_enabled is ON in app_settings, this snapshot is the
-- single source of truth for what the rider pays — no recalculation
-- at ride completion regardless of distance or time deviation.
-- SK regulatory requirement: the price shown before the ride is the
-- price charged.

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS fare_breakdown_snapshot JSONB DEFAULT NULL;

COMMENT ON COLUMN rides.fare_breakdown_snapshot IS
  'Fare breakdown as shown to rider at booking. When fare lock is active, this is the charged amount.';
