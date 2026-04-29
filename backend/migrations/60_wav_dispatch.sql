-- Migration 59: WAV dispatch support (L-P0-3 / Saskatchewan Transportation Act).
-- =============================================================================
-- Rollback plan:
--   ALTER TABLE drivers DROP COLUMN IF EXISTS is_wav;
--   ALTER TABLE rides   DROP COLUMN IF EXISTS requires_wav;
--   DROP INDEX IF EXISTS idx_drivers_is_wav;
-- Both columns have DEFAULT FALSE so existing rows are unaffected.
-- Old backend (without WAV feature) ignores the new columns harmlessly.
--
-- Purpose: Saskatchewan Transportation Act requires WAV (wheelchair-accessible
-- vehicle) ride requests to be matched to WAV-capable drivers when one is
-- online in the service area.
--
-- is_wav (drivers): driver self-declares or admin flags that their vehicle is
--   wheelchair-accessible. Defaults to false; changing it is rider-invisible.
--
-- requires_wav (rides): set by the rider at booking time when they need a WAV.
--   Defaults to false; the dispatch layer filters candidate drivers to is_wav=true
--   when this flag is set. If no WAV driver is available, the ride auto-cancels
--   via the existing 5-minute no-driver timeout (cancellation_type='no_drivers_found').
--
-- RLS: both columns ride the existing table-level RLS policies:
--   - drivers: service role reads/writes; driver can read own row.
--   - rides:   service role reads/writes; rider can read own row.
-- No additional RLS policies are needed.

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS is_wav BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS requires_wav BOOLEAN NOT NULL DEFAULT false;

-- Partial index so the dispatch query "find WAV-capable online drivers" stays
-- fast even as the drivers table grows. Most drivers will be is_wav=false, so
-- the partial index is small and cache-friendly.
CREATE INDEX IF NOT EXISTS idx_drivers_is_wav
  ON drivers (is_wav)
  WHERE is_wav = true;

COMMENT ON COLUMN drivers.is_wav IS
  'Vehicle is wheelchair-accessible (WAV). Set by driver or admin. Required '
  'for matching when a ride has requires_wav=true (SK Transportation Act).';

COMMENT ON COLUMN rides.requires_wav IS
  'Rider requested a wheelchair-accessible vehicle. When true, dispatch limits '
  'candidates to drivers with is_wav=true.';
