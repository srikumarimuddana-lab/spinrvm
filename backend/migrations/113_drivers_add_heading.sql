-- 113_drivers_add_heading.sql
-- Persist the driver's current compass heading (degrees, 0–359) on the
-- drivers row so rider/admin map markers can rotate the car icon to point
-- in the real direction of travel.
--
-- Background:
--   The driver app already sends `heading` in /location-batch payloads and
--   on Go Online, but the backend discarded it ("column might not exist —
--   currently causing 500"). As a result /drivers/nearby always returned
--   heading=null, and the rider home map rendered every car at rotation 0.
--   Two drivers at (or near) the same point therefore overlapped into a
--   single indistinguishable marker. This column closes that gap.
--
-- Type choice:
--   DOUBLE PRECISION (not int) — GPS heading arrives fractional from the
--   device. NULL is allowed and means "no heading yet" (freshly online,
--   stationary, or pre-migration row); readers fall back to 0/random.
--
-- Forward-compatibility:
--   Pure ADD COLUMN with a NULL default — instant metadata-only change in
--   Postgres, safe against in-flight production traffic. No backfill: there
--   is no historical heading to recover, and NULL is truthful.
--
-- Rollback:
--   ALTER TABLE public.drivers DROP COLUMN IF EXISTS heading;

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS heading DOUBLE PRECISION NULL;

COMMENT ON COLUMN public.drivers.heading IS
    'Last reported GPS compass heading in degrees (0–359). NULL = no heading yet. Written by /location-batch and Go Online; surfaced to rider/admin maps to rotate the car marker.';
