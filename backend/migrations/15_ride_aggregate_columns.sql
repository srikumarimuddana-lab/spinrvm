-- Migration: Add aggregate columns to rides for fast historical display
-- Raw driver_location_history is kept for 30 days, then deleted by a cleanup job.
-- These columns are populated once on ride completion so admin displays don't
-- need to join against driver_location_history at all.

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS planned_distance_km FLOAT;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS actual_distance_km FLOAT;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS pickup_to_driver_km FLOAT;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS phase_distances JSONB DEFAULT '{}'::JSONB;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS route_polyline JSONB DEFAULT '[]'::JSONB;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS gps_points_count INTEGER DEFAULT 0;

COMMENT ON COLUMN rides.planned_distance_km IS
  'Straight-line haversine distance between pickup and dropoff at ride creation time.';

COMMENT ON COLUMN rides.actual_distance_km IS
  'Actual GPS-tracked distance driven during trip_in_progress phase. Computed once on ride completion.';

COMMENT ON COLUMN rides.pickup_to_driver_km IS
  'Distance the driver traveled from their accept location to the pickup point.';

COMMENT ON COLUMN rides.phase_distances IS
  'Per-phase distance breakdown in km: {navigating_to_pickup, arrived_at_pickup, trip_in_progress, online_idle}';

COMMENT ON COLUMN rides.route_polyline IS
  'Downsampled GPS polyline (max ~200 points) as [[lat,lng], ...] for fast route rendering without querying driver_location_history.';

COMMENT ON COLUMN rides.gps_points_count IS
  'Total number of raw GPS points captured for this ride before downsampling.';
