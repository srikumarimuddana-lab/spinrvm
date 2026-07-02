-- Rollback (on paper): DROP INDEX CONCURRENTLY IF EXISTS idx_rides_service_area_created
--
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
-- CONCURRENTLY (as in migrations 156/177) so the build takes no write lock
-- on the hot rides table.
--
-- NOTE on comment style: because this file contains CONCURRENTLY, the
-- runner executes it through the autocommit path, which splits the file on
-- semicolons and strips only leading double-dash comment lines. Therefore
-- comments in this file must be double-dash lines containing no semicolons
-- and no C-style block comments — either of those gets split mid-comment
-- and fails the whole migration run.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_service_area_created
    ON rides (service_area_id, created_at DESC);
