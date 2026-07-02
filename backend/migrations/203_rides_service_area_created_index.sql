/* Rollback: DROP INDEX CONCURRENTLY IF EXISTS idx_rides_service_area_created; */

-- Migration 203: index for per-service-area time-window queries on rides.
--
-- GET /drivers/demand-heatmap filters rides on service_area_id + a
-- created_at window (admin-configurable up to 30 days, migration 202) and
-- orders by created_at DESC. No existing index covers service_area_id, so
-- every heatmap call was a sequential scan — acceptable when the feature was
-- a hidden 7-day dump, not with per-area polling from every idle driver.
-- The surge engine's demand counting (service_area_id + ride_requested_at)
-- also benefits from the leading column.
--
-- CONCURRENTLY matches the established pattern (migrations 114/156/177) so
-- the build takes no write lock on the hot rides table.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_service_area_created
    ON rides (service_area_id, created_at DESC);
