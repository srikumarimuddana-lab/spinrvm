-- Apply before backend rollout; per-area behavior ships disabled.
-- Immediate rollback: UPDATE service_areas SET scheduled_ride_config = '{}';
-- Never reset already-dispatched rides. After restoring old code, schema rollback:
-- DROP INDEX CONCURRENTLY IF EXISTS public.idx_rides_scheduled_pretrip;
-- ALTER TABLE public.service_areas DROP COLUMN scheduled_ride_config;
-- ALTER TABLE public.rides DROP COLUMN scheduled_driver_reminder_driver_id;
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE public.service_areas ADD COLUMN IF NOT EXISTS scheduled_ride_config jsonb NOT NULL DEFAULT '{}';
ALTER TABLE public.rides ADD COLUMN IF NOT EXISTS scheduled_driver_reminder_driver_id uuid;
DO $$
BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'service_area_scheduled_config_valid'
    AND conrelid = 'public.service_areas'::regclass) THEN
ALTER TABLE public.service_areas ADD CONSTRAINT service_area_scheduled_config_valid CHECK (
    jsonb_typeof(scheduled_ride_config) = 'object'
    AND scheduled_ride_config - ARRAY['enabled','dispatch_lead_minutes','driver_reminder_minutes','rider_reminder_minutes'] = '{}'::jsonb
    AND (NOT scheduled_ride_config ? 'enabled' OR jsonb_typeof(scheduled_ride_config->'enabled') = 'boolean')
    AND (NOT scheduled_ride_config ? 'dispatch_lead_minutes' OR (
        jsonb_typeof(scheduled_ride_config->'dispatch_lead_minutes') = 'number'
        AND (scheduled_ride_config->>'dispatch_lead_minutes')::numeric BETWEEN 0 AND 30
        AND (scheduled_ride_config->>'dispatch_lead_minutes')::numeric = trunc((scheduled_ride_config->>'dispatch_lead_minutes')::numeric)))
    AND (NOT scheduled_ride_config ? 'driver_reminder_minutes' OR (
        jsonb_typeof(scheduled_ride_config->'driver_reminder_minutes') = 'number'
        AND (scheduled_ride_config->>'driver_reminder_minutes')::numeric BETWEEN 1 AND 60
        AND (scheduled_ride_config->>'driver_reminder_minutes')::numeric = trunc((scheduled_ride_config->>'driver_reminder_minutes')::numeric)))
    AND (NOT scheduled_ride_config ? 'rider_reminder_minutes' OR (
        jsonb_typeof(scheduled_ride_config->'rider_reminder_minutes') = 'number'
        AND (scheduled_ride_config->>'rider_reminder_minutes')::numeric BETWEEN 1 AND 60
        AND (scheduled_ride_config->>'rider_reminder_minutes')::numeric = trunc((scheduled_ride_config->>'rider_reminder_minutes')::numeric)))
);
END IF;
END $$;
COMMIT;
-- On interrupted concurrent index build, inspect pg_index.indisvalid. If false,
-- DROP INDEX CONCURRENTLY public.idx_rides_scheduled_pretrip then rerun this file.
-- IF NOT EXISTS does not repair an invalid index; preceding steps are replay-safe.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_scheduled_pretrip ON public.rides (scheduled_time)
    WHERE is_scheduled = true AND status IN ('scheduled','searching','driver_assigned','driver_accepted','driver_arrived');
