-- 243_ride_routes_snapshot_attempts.sql
-- Attempt counter for route-snapshot rendering. The snapshot pipeline now
-- defers (schedules a finalizer retry) instead of silently falling back to
-- the OSM renderer when Google Static Maps fails while a key is configured;
-- this counter bounds the retries before the explicit OSM last resort.
--
-- Rollback plan:
-- Revert the renderer-policy code, then
--   ALTER TABLE public.ride_routes DROP COLUMN IF EXISTS snapshot_attempts;
-- No data dependency: the column only gates retry escalation.

ALTER TABLE public.ride_routes
    ADD COLUMN IF NOT EXISTS snapshot_attempts integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.ride_routes.snapshot_attempts IS
    'Consecutive failed Google snapshot render attempts for the current route revision; reset on success or new revision.';

NOTIFY pgrst, 'reload schema';
