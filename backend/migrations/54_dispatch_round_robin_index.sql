-- Migration 54: index for round-robin dispatch last-assigned lookup
-- Rollback: DROP INDEX CONCURRENTLY IF EXISTS idx_rides_driver_assigned_at;
--
-- Without this index, last_assigned_driver_id() does a sequential scan of the
-- entire rides table on every dispatch decision. At ~100k rides that blows the
-- 2s P95 dispatch SLA. The partial index only covers assigned rows so it stays
-- small and cache-hot even as cancelled/searching rows accumulate.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_driver_assigned_at
    ON rides (driver_id, assigned_at DESC)
    WHERE driver_id IS NOT NULL;
