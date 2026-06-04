-- 129_ride_routes_pickup_road_polyline.sql
-- Adds the OSRM road-matched polyline for the PICKUP leg (Phase 2,
-- driver→pickup) to the ride_routes side-table. Until now only the trip leg
-- (Phase 3, pickup→dropoff) was road-snapped into `road_polyline`; the admin
-- ride-detail map had nothing but raw GPS breadcrumbs (or nothing at all) for
-- the pickup leg. `road_polyline_pickup` mirrors `road_polyline` but for the
-- navigating_to_pickup phase so the admin map can draw a clean road-following
-- line for the driver's approach to the rider.
--
-- Same retention class as the other geometry: GPS-derived PII. It is scrubbed
-- at the 3-year mark by purge_pii_retention (Step I), alongside phase_polylines
-- and road_polyline. This migration re-forks purge_pii_retention VERBATIM from
-- migration 117 (the current authoritative definition — 118/120 did not
-- redefine it) and changes ONLY Step I to also clear the new column. Diff-verify
-- per docs/runbooks/migration-conflict-detection.md.
--
-- Rollback:
--   ALTER TABLE ride_routes DROP COLUMN IF EXISTS road_polyline_pickup;
--   then re-apply migration 117's purge_pii_retention (drop the new column from
--   Step I's UPDATE/WHERE).

ALTER TABLE ride_routes
    ADD COLUMN IF NOT EXISTS road_polyline_pickup jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN ride_routes.road_polyline_pickup IS
    'OSRM road-matched [[lat,lng],...] line for the navigating_to_pickup leg (Phase 2). Mirrors road_polyline (trip leg). GPS-derived PII — cleared at 3y by purge_pii_retention Step I.';

CREATE OR REPLACE FUNCTION purge_pii_retention(p_dry_run BOOLEAN DEFAULT false)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_started_at         TIMESTAMPTZ := now();
    v_rides_anonymized   INTEGER := 0;
    v_rides_deleted      INTEGER := 0;
    v_loc_deleted        INTEGER := 0;
    v_msgs_deleted       INTEGER := 0;
    v_tokens_deleted     INTEGER := 0;
    v_stripe_deleted     INTEGER := 0;
    v_audit_deleted      INTEGER := 0;
    v_dsar_purged        INTEGER := 0;
    v_routes_anonymized  INTEGER := 0;
    v_result             JSONB;

    c_gps_anon_age       INTERVAL := INTERVAL '3 years';
    c_ride_keep_age      INTERVAL := INTERVAL '7 years';
    c_loc_history_age    INTERVAL := INTERVAL '90 days';
    c_chat_age           INTERVAL := INTERVAL '90 days';
    c_token_grace_age    INTERVAL := INTERVAL '30 days';
    c_stripe_event_age   INTERVAL := INTERVAL '90 days';
    c_audit_log_age      INTERVAL := INTERVAL '7 years';
BEGIN
    -- Step A: anonymize ride GPS at 3y.
    IF NOT p_dry_run THEN
        UPDATE rides
        SET pickup_lat         = NULL,
            pickup_lng         = NULL,
            dropoff_lat        = NULL,
            dropoff_lng        = NULL,
            route_polyline     = '[]'::jsonb,
            phase_polylines    = '{}'::jsonb,
            route_snapshot_url = NULL,
            gps_anonymized_at  = v_started_at
        WHERE created_at < v_started_at - c_gps_anon_age
          AND gps_anonymized_at IS NULL;
        GET DIAGNOSTICS v_rides_anonymized = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_rides_anonymized
        FROM rides
        WHERE created_at < v_started_at - c_gps_anon_age
          AND gps_anonymized_at IS NULL;
    END IF;

    -- Step B: hard-delete rides at 7y.
    IF NOT p_dry_run THEN
        DELETE FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
        GET DIAGNOSTICS v_rides_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_rides_deleted
        FROM rides WHERE created_at < v_started_at - c_ride_keep_age;
    END IF;

    -- Step C: delete driver_location_history at 90d.
    IF NOT p_dry_run THEN
        DELETE FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
        GET DIAGNOSTICS v_loc_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_loc_deleted
        FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
    END IF;

    -- Step D: delete ride_messages at 90d.
    IF NOT p_dry_run THEN
        DELETE FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
        GET DIAGNOSTICS v_msgs_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_msgs_deleted
        FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
    END IF;

    -- Step E: delete revoked/expired refresh_tokens after 30d grace period.
    IF NOT p_dry_run THEN
        DELETE FROM refresh_tokens
        WHERE revoked_at IS NOT NULL
          AND revoked_at < v_started_at - c_token_grace_age;
        GET DIAGNOSTICS v_tokens_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_tokens_deleted
        FROM refresh_tokens
        WHERE revoked_at IS NOT NULL
          AND revoked_at < v_started_at - c_token_grace_age;
    END IF;

    -- Step F: delete stripe_events at 90d.
    IF NOT p_dry_run THEN
        DELETE FROM stripe_events
        WHERE created_at < v_started_at - c_stripe_event_age;
        GET DIAGNOSTICS v_stripe_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_stripe_deleted
        FROM stripe_events
        WHERE created_at < v_started_at - c_stripe_event_age;
    END IF;

    -- Step G: delete audit_logs at 7y (gated by session-flag).
    IF NOT p_dry_run AND current_setting('spinr.audit_logs.allow_delete', true) = 'true' THEN
        DELETE FROM audit_logs
        WHERE created_at < v_started_at - c_audit_log_age;
        GET DIAGNOSTICS v_audit_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_audit_deleted
        FROM audit_logs
        WHERE created_at < v_started_at - c_audit_log_age;
    END IF;

    -- Step H: anonymize DSAR pending-deletion users after 30d grace period.
    IF NOT p_dry_run THEN
        UPDATE users
        SET phone        = NULL,
            email        = NULL,
            first_name   = 'Deleted',
            last_name    = 'User',
            deleted_at   = v_started_at,
            deletion_scheduled_at = NULL
        WHERE deletion_scheduled_at IS NOT NULL
          AND deletion_scheduled_at <= v_started_at;
        GET DIAGNOSTICS v_dsar_purged = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_dsar_purged
        FROM users
        WHERE deletion_scheduled_at IS NOT NULL
          AND deletion_scheduled_at <= v_started_at;
    END IF;

    -- Step I (migration 117, extended by migration 129): clear ride_routes GPS
    -- geometry at 3y, mirroring the rides GPS anonymization (Step A). Keeps
    -- per-phase distance/duration scalars for the 7y SGI window; the row itself
    -- cascades on the 7y rides delete. Migration 129 adds road_polyline_pickup
    -- to the cleared set and the non-empty gate so re-runs stay no-ops.
    IF NOT p_dry_run THEN
        UPDATE ride_routes
        SET phase_polylines       = '{}'::jsonb,
            road_polyline         = '[]'::jsonb,
            road_polyline_pickup  = '[]'::jsonb
        WHERE computed_at < v_started_at - c_gps_anon_age
          AND (phase_polylines <> '{}'::jsonb
               OR road_polyline <> '[]'::jsonb
               OR road_polyline_pickup <> '[]'::jsonb);
        GET DIAGNOSTICS v_routes_anonymized = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_routes_anonymized
        FROM ride_routes
        WHERE computed_at < v_started_at - c_gps_anon_age
          AND (phase_polylines <> '{}'::jsonb
               OR road_polyline <> '[]'::jsonb
               OR road_polyline_pickup <> '[]'::jsonb);
    END IF;

    v_result := jsonb_build_object(
        'started_at',                 v_started_at,
        'completed_at',               now(),
        'dry_run',                    p_dry_run,
        'rides_anonymized',           v_rides_anonymized,
        'rides_deleted',              v_rides_deleted,
        'driver_location_deleted',    v_loc_deleted,
        'ride_messages_deleted',      v_msgs_deleted,
        'refresh_tokens_deleted',     v_tokens_deleted,
        'stripe_events_deleted',      v_stripe_deleted,
        'audit_logs_deleted',         v_audit_deleted,
        'dsar_users_purged',          v_dsar_purged,
        'ride_routes_anonymized',     v_routes_anonymized
    );

    -- Audit trail — uses actor_id (migration 57 schema); user_email was
    -- dropped by migration 51.
    IF NOT p_dry_run THEN
        INSERT INTO audit_logs (id, action, entity_type, entity_id, actor_id, details, created_at)
        VALUES (
            gen_random_uuid()::text,
            'pii_retention_purge',
            'system',
            v_started_at::text,
            'system:retention_purge',
            v_result::text,
            now()
        );
    END IF;

    RETURN v_result;
END;
$$;

REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role;

COMMENT ON FUNCTION purge_pii_retention(BOOLEAN) IS
    'B-P1-6 + B-P1-7 + B-P2-4 + DV-8 retention enforcement. Uses actor_id (migration 57 schema). Step I (migration 117, extended 129) clears ride_routes geometry — phase_polylines, road_polyline, road_polyline_pickup — at 3y. p_dry_run=true returns counts without mutating.';

NOTIFY pgrst, 'reload schema';
