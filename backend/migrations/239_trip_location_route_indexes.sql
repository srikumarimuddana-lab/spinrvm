-- 239_trip_location_route_indexes.sql
--
-- Add the high-write trip-location indexes outside a transaction. The migration
-- runner detects CONCURRENTLY and uses autocommit, so production GPS writes can
-- continue while these indexes are built.
--
-- Rollback plan:
--   DROP INDEX CONCURRENTLY IF EXISTS public.uq_dlh_ride_driver_session_sequence;
--   DROP INDEX CONCURRENTLY IF EXISTS public.idx_dlh_ride_captured;
--   DROP INDEX CONCURRENTLY IF EXISTS public.idx_ride_routes_processing;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_dlh_ride_driver_session_sequence
    ON public.driver_location_history(ride_id, driver_id, recording_session_id, sequence_number);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dlh_ride_captured
    ON public.driver_location_history(ride_id, captured_at, recording_session_id, sequence_number);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_routes_processing
    ON public.ride_routes(processing_status, next_retry_at, computed_at);

NOTIFY pgrst, 'reload schema';
