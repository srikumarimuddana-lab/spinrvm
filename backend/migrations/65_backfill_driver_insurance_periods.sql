-- 65_backfill_driver_insurance_periods.sql
-- M-5d: One-shot backfill for the new driver_insurance_periods table.
--
-- Migration 64 created the table; helper + state-machine hooks (M-5b/c)
-- record transitions going forward. This file seeds an open row for every
-- driver currently online (period 1) or currently driving a ride
-- (period 2 / period 3) so the audit log is complete from day one.
--
-- Without this backfill, drivers already online when 64 ships would have
-- no open period row until they next toggle offline → online — which means
-- an indefinite gap of unlogged commercial-insurance windows. Bad for SGI.
--
-- Idempotent — the partial unique index `driver_insurance_periods_open`
-- (one open row per driver) plus the explicit NOT EXISTS guards mean
-- re-running this is a no-op. The migration runner additionally tracks
-- this filename in schema_migrations so it executes exactly once anyway.
--
-- Rollback: TRUNCATE driver_insurance_periods; (only safe before
-- production traffic; after that, use the table's normal lifecycle).
--
-- Insertion order matters: higher-priority periods first, so the
-- NOT EXISTS guard on lower-priority queries naturally skips drivers
-- already covered by a higher period in the same migration.

-- Period 3: drivers currently on an in_progress ride.
-- Passenger-aboard takes precedence over the driver's online flag.
INSERT INTO driver_insurance_periods (driver_id, period, ride_id, started_at)
SELECT
    r.driver_id,
    3,
    r.id,
    COALESCE(r.ride_started_at, now())
FROM rides r
WHERE r.status = 'in_progress'
  AND r.driver_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM driver_insurance_periods dip
      WHERE dip.driver_id = r.driver_id AND dip.ended_at IS NULL
  );

-- Period 2: drivers currently assigned/accepted/arrived but not yet started.
-- No dedicated `driver_assigned_at` column exists on rides; fall back through
-- the closest available timestamps (driver_arrived_at → driver_accepted_at →
-- created_at) so started_at is never NULL and reflects the earliest moment
-- the driver was committed to the ride that we can prove from the data.
INSERT INTO driver_insurance_periods (driver_id, period, ride_id, started_at)
SELECT
    r.driver_id,
    2,
    r.id,
    COALESCE(r.driver_arrived_at, r.driver_accepted_at, r.created_at, now())
FROM rides r
WHERE r.status IN ('driver_assigned', 'driver_accepted', 'driver_arrived')
  AND r.driver_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM driver_insurance_periods dip
      WHERE dip.driver_id = r.driver_id AND dip.ended_at IS NULL
  );

-- Period 1: drivers online but not on a ride. The NOT EXISTS guard
-- naturally skips anyone we just inserted as period 2 or 3.
INSERT INTO driver_insurance_periods (driver_id, period, started_at)
SELECT
    d.id,
    1,
    now()
FROM drivers d
WHERE d.is_online = true
  AND NOT EXISTS (
      SELECT 1 FROM driver_insurance_periods dip
      WHERE dip.driver_id = d.id AND dip.ended_at IS NULL
  );
