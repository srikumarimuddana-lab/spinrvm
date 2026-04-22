-- Migration 39: per-phase polylines and durations on rides
--
-- Today the ride row stores `phase_distances` (per-phase km) and a
-- single combined `route_polyline` with phase tags inline. SGI / ops
-- want to see the Phase 2 (driver→pickup) and Phase 3 (pickup→dropoff)
-- routes separately on a map and to know how long each phase took, not
-- just how far the vehicle moved.
--
-- Add two JSONB columns:
--   phase_durations  — seconds spent in each phase, e.g.
--                      {"navigating_to_pickup": 312, "trip_in_progress": 1284}
--   phase_polylines  — per-phase downsampled GPS path (max ~150 pts/phase)
--                      {"navigating_to_pickup": [[lat,lng,iso_ts], ...],
--                       "trip_in_progress":     [[lat,lng,iso_ts], ...]}
--
-- The legacy `route_polyline` column is kept and still written so the
-- existing ride-detail map renderer keeps working unchanged.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS phase_durations JSONB DEFAULT '{}'::JSONB;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS phase_polylines JSONB DEFAULT '{}'::JSONB;

COMMENT ON COLUMN rides.phase_durations IS
    'Seconds spent in each tracking phase. Keys: navigating_to_pickup, arrived_at_pickup, trip_in_progress.';
COMMENT ON COLUMN rides.phase_polylines IS
    'Downsampled GPS path per phase: {phase: [[lat,lng,iso_ts], ...]}. Phase 1 (online_idle) is not stored per-ride.';
