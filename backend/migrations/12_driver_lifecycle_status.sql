-- Migration: Add driver lifecycle status columns

-- Main status field (if not exists — some deployments may already have it)
ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';

-- Action tracking
ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS suspension_reason TEXT;
ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS ban_reason TEXT;
ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS banned_at TIMESTAMPTZ;

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS unban_reason TEXT;
ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS unbanned_at TIMESTAMPTZ;

-- Index for status filtering
CREATE INDEX IF NOT EXISTS idx_drivers_status ON drivers(status);

-- Backfill: set existing verified drivers to 'active', unverified to 'pending'
UPDATE drivers SET status = 'active' WHERE is_verified = TRUE AND status = 'pending';
