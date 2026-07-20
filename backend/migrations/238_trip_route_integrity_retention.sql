-- 238_trip_route_integrity_retention.sql
-- Enforce the three-year PIPEDA / Saskatchewan GPS retention window for the
-- v2 segmented route contract and its timestamp-only gap-audit companion.
--
-- Rollback plan:
-- Stop the daily retention loop before dropping purge_trip_route_geometry.
-- The anonymization marker and cleared geometry are intentionally irreversible
-- without point-in-time recovery; do not restore stale route coordinates.

ALTER TABLE public.ride_routes
    ADD COLUMN IF NOT EXISTS route_geometry_anonymized_at timestamptz,
    ADD COLUMN IF NOT EXISTS snapshot_purge_pending_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_ride_routes_geometry_unanonymized
    ON public.ride_routes(finalized_at)
    WHERE route_geometry_anonymized_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_ride_location_gap_events_detected_at
    ON public.ride_location_gap_events(detected_at);

CREATE OR REPLACE FUNCTION purge_trip_route_geometry(p_dry_run boolean DEFAULT false)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_started_at timestamptz := now();
    v_routes_anonymized integer := 0;
    v_snapshot_objects_pending integer := 0;
    v_gap_events_deleted integer := 0;
    v_result jsonb;
    c_gps_age interval := INTERVAL '3 years';
    c_trip_audit_age interval := INTERVAL '7 years';
BEGIN
    -- Route geometry, final completion fix, and snapshot images all reveal the
    -- rider's exact travel path. The backend deletes private storage objects
    -- before clearing their path, then re-invokes this function to mark the
    -- route anonymized. A failed Storage deletion therefore remains durable and
    -- replayable instead of orphaning an inaccessible-but-retained image.
    IF NOT p_dry_run THEN
        UPDATE public.ride_routes
        SET observed_segments = '[]'::jsonb,
            road_matched_segments = '[]'::jsonb,
            completion_point = NULL,
            snapshot_url = NULL,
            snapshot_revision = 0,
            snapshot_purge_pending_at = v_started_at
        WHERE COALESCE(finalized_at, computed_at) < v_started_at - c_gps_age
          AND route_geometry_anonymized_at IS NULL
          AND snapshot_object_path IS NOT NULL
          AND snapshot_purge_pending_at IS NULL;
        GET DIAGNOSTICS v_snapshot_objects_pending = ROW_COUNT;

        UPDATE public.ride_routes
        SET observed_segments = '[]'::jsonb,
            road_matched_segments = '[]'::jsonb,
            completion_point = NULL,
            snapshot_url = NULL,
            snapshot_object_path = NULL,
            snapshot_revision = 0,
            snapshot_purge_pending_at = NULL,
            route_geometry_anonymized_at = v_started_at
        WHERE COALESCE(finalized_at, computed_at) < v_started_at - c_gps_age
          AND route_geometry_anonymized_at IS NULL
          AND snapshot_object_path IS NULL;
        GET DIAGNOSTICS v_routes_anonymized = ROW_COUNT;

        -- This audit table has no coordinates, but it is trip-linked and no
        -- longer needed once the seven-year trip/audit record ages out.
        DELETE FROM public.ride_location_gap_events
        WHERE detected_at < v_started_at - c_trip_audit_age;
        GET DIAGNOSTICS v_gap_events_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_routes_anonymized
        FROM public.ride_routes
        WHERE COALESCE(finalized_at, computed_at) < v_started_at - c_gps_age
          AND route_geometry_anonymized_at IS NULL;

        SELECT COUNT(*) INTO v_snapshot_objects_pending
        FROM public.ride_routes
        WHERE COALESCE(finalized_at, computed_at) < v_started_at - c_gps_age
          AND route_geometry_anonymized_at IS NULL
          AND snapshot_object_path IS NOT NULL;

        SELECT COUNT(*) INTO v_gap_events_deleted
        FROM public.ride_location_gap_events
        WHERE detected_at < v_started_at - c_trip_audit_age;
    END IF;

    v_result := jsonb_build_object(
        'started_at', v_started_at,
        'completed_at', now(),
        'dry_run', p_dry_run,
        'ride_routes_anonymized', v_routes_anonymized,
        'route_snapshot_objects_pending', v_snapshot_objects_pending,
        'ride_location_gap_events_deleted', v_gap_events_deleted
    );
    RETURN v_result;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.purge_trip_route_geometry(boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.purge_trip_route_geometry(boolean) TO service_role;

COMMENT ON FUNCTION public.purge_trip_route_geometry(boolean) IS
    'Removes v2 route coordinates and completion fixes at 3 years after the retention worker deletes each private route snapshot object; deletes timestamp-only GPS-gap events at 7 years. Replay-safe daily retention companion.';

NOTIFY pgrst, 'reload schema';
