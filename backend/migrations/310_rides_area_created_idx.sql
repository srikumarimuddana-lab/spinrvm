/* Rollback: DROP INDEX CONCURRENTLY IF EXISTS idx_rides_area_created; */

-- The driver demand-heatmap endpoint (GET /drivers/demand-heatmap) filters
-- rides by (service_area_id, created_at) over a 7-day window. Without a
-- composite index the planner falls back to a sequential scan or picks only
-- one of the two single-column indexes. This index covers that query pattern.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_area_created
    ON public.rides (service_area_id, created_at DESC);
