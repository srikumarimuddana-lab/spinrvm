/* Rollback: DROP INDEX CONCURRENTLY IF EXISTS idx_rides_rider_history_created_id; */

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_rider_history_created_id
    ON rides (rider_id, created_at DESC, id DESC)
    WHERE status = 'completed'
       OR (status = 'cancelled' AND driver_id IS NOT NULL);
