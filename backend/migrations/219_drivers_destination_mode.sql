-- 219_drivers_destination_mode.sql
-- =============================================================================
-- Purpose: Add the driver "destination mode" columns to the drivers table.
--
-- The destination-mode feature ships in the backend already:
--   - routes/drivers.py    : POST/DELETE/GET /drivers/destination read & write
--                            destination_mode / destination_address /
--                            destination_lat / destination_lng.
--   - services/dispatch_service.py : _ride_brings_driver_closer_to_destination
--                            reads destination_mode + destination_lat/lng to
--                            gate offers for drivers heading home.
--   - routes/rides.py      : the dispatch candidate query AND the /rides/estimate
--                            candidate query both SELECT destination_mode,
--                            destination_lat, destination_lng in their column
--                            projection.
--
-- But no migration ever added these columns, so against a real database every
-- SELECT that lists them aborts with:
--       column drivers.destination_mode does not exist
-- That failure takes down the whole /rides/estimate query, which is why the
-- rider app shows no vehicle types and surfaces a "destination_mode does not
-- exist" error in the fare-estimate panel. This migration closes the gap.
--
-- Forward-compatible: all columns are additive and nullable / DEFAULT false,
-- so existing rows are unaffected and in-flight traffic (old backend that does
-- not reference these columns) keeps working. No table rewrite: a nullable
-- ADD COLUMN and a boolean ADD COLUMN with a constant DEFAULT are both
-- metadata-only in Postgres 11+.
--
-- RLS: the columns ride the existing drivers table-level RLS policies (service
-- role reads/writes; a driver may read their own row). No new policy needed.
--
-- Rollback plan:
--   ALTER TABLE drivers DROP COLUMN IF EXISTS destination_mode;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS destination_address;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS destination_lat;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS destination_lng;
-- =============================================================================

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS destination_mode BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS destination_address TEXT;

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS destination_lat DOUBLE PRECISION;

ALTER TABLE drivers
  ADD COLUMN IF NOT EXISTS destination_lng DOUBLE PRECISION;

COMMENT ON COLUMN drivers.destination_mode IS
  'Driver tapped "Heading home": dispatch only offers rides whose dropoff '
  'brings them closer to destination_lat/lng (services/dispatch_service.py).';
COMMENT ON COLUMN drivers.destination_address IS
  'Human-readable destination the driver set for destination mode (driver-only, '
  'never shared with riders).';
COMMENT ON COLUMN drivers.destination_lat IS
  'Latitude of the driver destination-mode target; NULL when not set.';
COMMENT ON COLUMN drivers.destination_lng IS
  'Longitude of the driver destination-mode target; NULL when not set.';
