-- Migration 68: composite indexes for ride history cursor pagination and active-ride lookup
-- Rollback: DROP INDEX IF EXISTS idx_rides_rider_status_created, idx_rides_rider_active_status;

-- Supports GET /rides/history: WHERE rider_id = ? AND status IN ('completed','cancelled')
--   ORDER BY created_at DESC LIMIT n
-- Without this index Postgres falls back to a sequential scan on the rides table,
-- which degrades as ride volume grows. The partial index on the two terminal
-- statuses keeps the index small (active rides are ephemeral).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_rider_status_created
    ON rides (rider_id, created_at DESC)
    WHERE status IN ('completed', 'cancelled');

-- Supports the at-most-one-active-ride invariant check:
--   WHERE rider_id = ? AND status = ANY(active_statuses)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_rider_active_status
    ON rides (rider_id, status)
    WHERE status IN ('searching', 'driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress');

-- Supports driver dashboard / dispatch queries filtered by driver_id + status:
--   WHERE driver_id = ? AND status IN ('driver_assigned','driver_accepted','driver_arrived','in_progress')
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_driver_active_status
    ON rides (driver_id, status)
    WHERE status IN ('driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress');
