-- 236_trip_route_integrity_retention.sql
-- Enforce the three-year PIPEDA / Saskatchewan GPS retention window for the
-- v2 segmented route contract and its timestamp-only gap-audit companion.
--
-- Rollback plan:
-- Stop the daily retention loop before dropping purge_trip_route_geometry.
-- The anonymization marker and cleared geometry are intentionally irreversible
-- without point-in-time recovery; do not restore stale route coordinates.

ALTER TABLE public.ride_routes
    ADD COLUMN IF NOT EXISTS route_geometry_anonymized_at timestamptz;

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
    v_gap_events_deleted integer := 0;
    v_result jsonb;
    c_gps_age interval := INTERVAL '3 years';
    c_trip_audit_age interval := INTERVAL '7 years';
BEGIN
    -- Route geometry, final completion fix, and snapshot URL all reveal the
    -- rider's exact travel path. Preserve scalar route-quality fields and
    -- revision counts for audit while removing every coordinate-bearing v2
    -- surface after the regulatory retention ceiling.
    IF NOT p_dry_run THEN
        UPDATE public.ride_routes
        SET observed_segments = '[]'::jsonb,
            road_matched_segments = '[]'::jsonb,
            completion_point = NULL,
            snapshot_url = NULL,
            snapshot_revision = 0,
            route_geometry_anonymized_at = v_started_at
        WHERE COALESCE(finalized_at, computed_at) < v_started_at - c_gps_age
          AND route_geometry_anonymized_at IS NULL;
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

        SELECT COUNT(*) INTO v_gap_events_deleted
        FROM public.ride_location_gap_events
        WHERE detected_at < v_started_at - c_trip_audit_age;
    END IF;

    v_result := jsonb_build_object(
        'started_at', v_started_at,
        'completed_at', now(),
        'dry_run', p_dry_run,
        'ride_routes_anonymized', v_routes_anonymized,
        'ride_location_gap_events_deleted', v_gap_events_deleted
    );
    RETURN v_result;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.purge_trip_route_geometry(boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.purge_trip_route_geometry(boolean) TO service_role;

COMMENT ON FUNCTION public.purge_trip_route_geometry(boolean) IS
    'Removes v2 route coordinates, completion fix and snapshot URL at 3 years; deletes timestamp-only GPS-gap events at 7 years. Replay-safe daily retention companion.';

NOTIFY pgrst, 'reload schema';
