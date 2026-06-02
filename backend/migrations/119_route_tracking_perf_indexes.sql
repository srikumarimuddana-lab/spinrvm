-- 119_route_tracking_perf_indexes.sql
-- Lightweight read-path indexes for route/admin audit tables.
-- These match current query patterns and future operational checks without adding
-- meaningful write overhead: driver_activity_log is append-only event data,
-- ride_routes is written once per completed ride, and failed route geometry rows
-- are rare but important for admin/support follow-up.

-- Admin driver activity endpoint filters by driver_id and orders newest-first.
CREATE INDEX IF NOT EXISTS idx_driver_activity_driver_created
    ON driver_activity_log(driver_id, created_at DESC);

-- Fast operational lookup for route geometry rows that failed to persist cleanly.
CREATE INDEX IF NOT EXISTS idx_ride_routes_failed_save
    ON ride_routes(ride_id)
    WHERE save_status = 'failed';

-- rides duplicates route_geometry_status for list/dispute views; make failed
-- geometry checks cheap without indexing every completed/saved ride.
CREATE INDEX IF NOT EXISTS idx_rides_route_geometry_failed
    ON rides(id)
    WHERE route_geometry_status = 'failed';
