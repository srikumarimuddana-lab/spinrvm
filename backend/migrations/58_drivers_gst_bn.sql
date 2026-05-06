-- Migration 58: Add GST registration fields to drivers table
--
-- Rollback: ALTER TABLE drivers DROP COLUMN IF EXISTS gst_registered;
--           ALTER TABLE drivers DROP COLUMN IF EXISTS gst_bn;
--
-- Purpose: Support T4A-compatible earnings export. CRA requires drivers who
-- earn above the T4A threshold to report GST/HST registration. Saskatchewan
-- Transportation Act + CRA guidance requires the platform to surface this
-- on the annual earnings export so drivers can file accurately.
--
-- gst_registered: driver self-attests they hold a GST/HST account.
-- gst_bn:         CRA Business Number in format 123456789RT0001 (nullable;
--                 only present when gst_registered = true).
--
-- These are public registration numbers, not PII — no vault encryption.
-- RLS: drivers can read/write their own row via existing policy.

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS gst_registered BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS gst_bn         TEXT;

-- Partial index for admin queries ("show me all GST-registered drivers")
CREATE INDEX IF NOT EXISTS idx_drivers_gst_registered
  ON drivers (gst_registered)
  WHERE gst_registered = true;

COMMENT ON COLUMN drivers.gst_registered IS
  'Driver holds a CRA GST/HST account. Required for T4A-compatible export.';
COMMENT ON COLUMN drivers.gst_bn IS
  'CRA Business Number (format 123456789RT0001). Null when gst_registered=false.';
