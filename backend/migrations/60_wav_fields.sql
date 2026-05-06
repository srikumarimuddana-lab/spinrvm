-- Migration 60: WAV (wheelchair-accessible vehicle) dispatch support
--
-- Saskatchewan Transportation Act s.22: ride-share operators must
-- accommodate passengers requiring wheelchair-accessible vehicles when
-- a WAV driver is online in the service area.
--
-- Rollback:
--   ALTER TABLE drivers DROP COLUMN IF EXISTS is_wav;
--   ALTER TABLE rides   DROP COLUMN IF EXISTS requires_wav;
--   DROP INDEX IF EXISTS idx_drivers_is_wav;
--
-- Forward-safety: both columns default to false, so existing rows
-- and in-flight code paths are unaffected.

-- 1. Flag on the driver row: set by admin dashboard on driver approval
--    once they confirm the vehicle has an approved wheelchair lift/ramp.
ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS is_wav BOOLEAN NOT NULL DEFAULT false;

-- 2. Flag on the ride row: set by the rider at booking time via the
--    "accessibility" toggle in ride-options.tsx.
ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS requires_wav BOOLEAN NOT NULL DEFAULT false;

-- 3. Partial index: only WAV-capable drivers need to be scanned when a
--    WAV ride comes in. At typical fleet size this keeps the dispatch
--    query sub-millisecond even before PostGIS is available.
CREATE INDEX IF NOT EXISTS idx_drivers_is_wav
  ON drivers (is_wav)
  WHERE is_wav = true;

-- 4. Composite index used by the dispatch get_rows call when
--    requires_wav=true: online + available + wav in one pass.
CREATE INDEX IF NOT EXISTS idx_drivers_dispatch_wav
  ON drivers (is_online, is_available, is_wav)
  WHERE is_online = true AND is_available = true AND is_wav = true;
