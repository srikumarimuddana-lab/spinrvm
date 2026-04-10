-- Migration: Add needs_review flag to drivers table

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS needs_review BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_drivers_needs_review ON drivers(needs_review)
  WHERE needs_review = TRUE;

COMMENT ON COLUMN drivers.needs_review IS
  'Set to true when a verified driver re-uploads documents or changes vehicle info. Admin clears it on re-approval.';
