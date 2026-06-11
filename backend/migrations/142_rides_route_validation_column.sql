-- 142_rides_route_validation_column.sql
-- Adds route_validation JSONB column to rides.
-- backend/routes/drivers.py stores the post-trip OSRM/Google Roads verdict here
-- (provider, total_points, snapped_points, deviation_pct, avg_deviation_m,
--  max_deviation_m, verdict). Without this column every completed ride with
-- ≥5 breadcrumbs produced a PGRST204 schema-cache error and lost the fraud signal.
--
-- Forward-compatible: nullable column, no NOT NULL / default change to existing rows.
--
-- Rollback:
--   ALTER TABLE rides DROP COLUMN IF EXISTS route_validation;

ALTER TABLE rides ADD COLUMN IF NOT EXISTS route_validation JSONB;

COMMENT ON COLUMN rides.route_validation IS
  'Post-trip road-network match result (provider, verdict, deviation_pct, etc.) '
  'written by the route_validation background task after ride completion.';
