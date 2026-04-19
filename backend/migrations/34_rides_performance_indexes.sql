-- Composite indexes for the two most common rides query patterns.
-- CONCURRENTLY avoids table locks in production.

CREATE INDEX CONCURRENTLY IF NOT EXISTS
  idx_rides_driver_created ON rides(driver_id, created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS
  idx_rides_rider_created ON rides(rider_id, created_at DESC);
