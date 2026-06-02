-- 117_ride_routes.sql
-- 1:1 side-table holding a completed ride's route detail (per-phase distances +
-- durations, per-phase GPS polylines, the OSRM road-matched line, point count).
-- Kept OUT of the hot `rides` row for locking/perf: written once at completion
-- (read-on-demand by the admin ride map modal), so the large polyline JSON never
-- bloats / TOASTs the rides row that dispatch, payment, and list reads touch.
--
-- rides keeps the billing scalar actual_distance_km (fare source of truth);
-- ride_routes holds the heavy detail. Read only via the admin ride-detail
-- endpoint (service role) — locked down to no anon/authenticated access.
--
-- Retention:
--   * ON DELETE CASCADE → cleared with the 7-year rides hard-delete.
--   * GPS geometry is PII → purge_pii_retention now clears the polylines at the
--     same 3-year mark as the rides GPS, keeping the per-phase distance SCALARS
--     (for the 7-year SGI/tax window). The function is forked verbatim from
--     migration 67 (the current authoritative definition) with one new step
--     (Step I) added — diff-verify per docs/runbooks/migration-conflict-detection.md.
--
-- Rollback:
--   DROP TABLE IF EXISTS ride_routes;
--   then re-apply migration 67's purge_pii_retention (drop Step I).

CREATE TABLE IF NOT EXISTS ride_routes (
    ride_id          text PRIMARY KEY REFERENCES rides(id) ON DELETE CASCADE,
    phase_distances  jsonb NOT NULL DEFAULT '{}'::jsonb,
    phase_durations  jsonb NOT NULL DEFAULT '{}'::jsonb,
    phase_polylines  jsonb NOT NULL DEFAULT '{}'::jsonb,   -- raw GPS per phase (PII, 3y)
    road_polyline    jsonb NOT NULL DEFAULT '[]'::jsonb,   -- OSRM road-matched [[lat,lng],...] (PII, 3y)
    gps_points_count integer NOT NULL DEFAULT 0,
    computed_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE ride_routes IS
    'Per-ride route detail (per-phase distances/durations/polylines + OSRM road-matched line), 1:1 with rides. Write-once at completion, read-on-demand by the admin map modal. Geometry cleared at 3y by purge_pii_retention; row cascades on the 7y rides delete.';

-- Index for the 3-year geometry-anonymization sweep (Step I below).
CREATE INDEX IF NOT EXISTS idx_ride_routes_computed_at ON ride_routes (computed_at);

-- Lock down: service role (backend) bypasses RLS by design; the frontend anon /
-- authenticated keys must never read route geometry directly. No permissive
-- policies = no anon/authenticated access; the admin endpoint reads via backend.
ALTER TABLE ride_routes ENABLE ROW LEVEL SECURITY;

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

    -- Step I (migration 117): clear ride_routes GPS geometry at 3y, mirroring
    -- the rides GPS anonymization (Step A). Keeps per-phase distance/duration
    -- scalars for the 7y SGI window; the row itself cascades on the 7y rides
    -- delete. Gated on non-empty geometry so re-runs are no-ops.
    IF NOT p_dry_run THEN
        UPDATE ride_routes
        SET phase_polylines = '{}'::jsonb,
            road_polyline    = '[]'::jsonb
        WHERE computed_at < v_started_at - c_gps_anon_age
          AND (phase_polylines <> '{}'::jsonb OR road_polyline <> '[]'::jsonb);
        GET DIAGNOSTICS v_routes_anonymized = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_routes_anonymized
        FROM ride_routes
        WHERE computed_at < v_started_at - c_gps_anon_age
          AND (phase_polylines <> '{}'::jsonb OR road_polyline <> '[]'::jsonb);
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
    'B-P1-6 + B-P1-7 + B-P2-4 + DV-8 retention enforcement. Uses actor_id (migration 57 schema). Step I (migration 117) clears ride_routes geometry at 3y. p_dry_run=true returns counts without mutating.';

NOTIFY pgrst, 'reload schema';
