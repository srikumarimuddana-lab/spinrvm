-- 53_rides_one_active_per_rider.sql
-- Rollback: DROP INDEX IF EXISTS rides_one_active_per_rider;
--
-- Enforces the CLAUDE.md invariant at the DB level:
--   "a rider may have at most one active ride at a time"
-- Closes the race window between the application-level SELECT check
-- and the subsequent INSERT in POST /rides (double-booking P0 fix).
--
-- Note: CREATE INDEX ... CONCURRENTLY cannot run inside a transaction.
-- This index is non-concurrent and holds a SHARE lock for its duration.
-- On a small/medium rides table this is sub-second. If the table exceeds
-- ~10M rows, run manually outside the migration runner with CONCURRENTLY.

CREATE UNIQUE INDEX IF NOT EXISTS rides_one_active_per_rider
  ON rides (rider_id)
  WHERE status IN (
    'searching',
    'driver_assigned',
    'driver_accepted',
    'driver_arrived',
    'in_progress'
  );
