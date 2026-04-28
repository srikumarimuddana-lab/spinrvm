-- Migration 57: Fix purge_pii_retention() audit_logs INSERT to match production schema.
-- =============================================================================
-- Problem: migration 56 introduced CREATE OR REPLACE for purge_pii_retention()
--   with an INSERT that uses non-existent columns (actor_id, actor_role, resource):
--
--       INSERT INTO audit_logs (actor_id, actor_role, action, resource, details)
--
--   The production audit_logs schema (migration 06 + 51_remove_user_email) is:
--       (id TEXT, action TEXT, entity_type TEXT, entity_id TEXT, details TEXT, created_at TIMESTAMPTZ)
--
--   This caused every daily retention tick to roll back the entire transaction,
--   silently breaking PIPEDA / SK Transportation Act retention enforcement.
--
-- Fix: CREATE OR REPLACE with the corrected INSERT column list.
--   All Steps A–G are identical to migration 56 — only the INSERT is changed.
--
-- Rollback: CREATE OR REPLACE with the migration 56 version (re-introduces bug;
--   only do this if migration 57 itself fails on a schema that has the old columns).
--
-- Idempotency: all UPDATE/DELETE steps gate on existing conditions; re-running
--   finds zero candidates and returns zero counts. The audit_logs INSERT uses a
--   fresh gen_random_uuid() so a re-run adds one extra row — acceptable.
-- =============================================================================

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
    v_dsar_purged        INTEGER := 0;
    v_result             JSONB;

    c_gps_anon_age       INTERVAL := INTERVAL '3 years';
    c_ride_keep_age      INTERVAL := INTERVAL '7 years';
    c_loc_history_age    INTERVAL := INTERVAL '90 days';
    c_chat_age           INTERVAL := INTERVAL '90 days';
    c_token_grace_age    INTERVAL := INTERVAL '30 days';
    c_stripe_event_age   INTERVAL := INTERVAL '90 days';
BEGIN
    -- Step A: anonymize ride GPS at 3 years.
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

    -- Step B: hard-delete rides at 7 years.
    IF NOT p_dry_run THEN
        DELETE FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
        GET DIAGNOSTICS v_rides_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_rides_deleted
        FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
    END IF;

    -- Step C: driver location history at 90 days.
    IF NOT p_dry_run THEN
        DELETE FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
        GET DIAGNOSTICS v_loc_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_loc_deleted
        FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
    END IF;

    -- Step D: ride chat at 90 days.
    IF NOT p_dry_run THEN
        DELETE FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
        GET DIAGNOSTICS v_msgs_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_msgs_deleted
        FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
    END IF;

    -- Step E: expired refresh tokens.
    IF NOT p_dry_run THEN
        DELETE FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
        GET DIAGNOSTICS v_tokens_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_tokens_deleted
        FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
    END IF;

    -- Step F: Stripe webhook idempotency rows at 90 days.
    IF NOT p_dry_run THEN
        DELETE FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
        GET DIAGNOSTICS v_stripe_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_stripe_deleted
        FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
    END IF;

    -- Step G: DSAR pending-deletion users whose 30-day grace period has elapsed.
    IF NOT p_dry_run THEN
        UPDATE users
        SET first_name  = NULL,
            last_name   = NULL,
            email       = NULL,
            phone       = NULL,
            avatar_url  = NULL,
            status      = 'deleted',
            deleted_at  = v_started_at
        WHERE status       = 'pending_deletion'
          AND deleted_at   IS NULL
          AND deletion_scheduled_at <= v_started_at;
        GET DIAGNOSTICS v_dsar_purged = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_dsar_purged
        FROM users
        WHERE status       = 'pending_deletion'
          AND deleted_at   IS NULL
          AND deletion_scheduled_at <= v_started_at;
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
        'dsar_users_purged',          v_dsar_purged
    );

    -- Fixed: use production audit_logs schema (migration 06 + 51_remove_user_email):
    --   (id, action, entity_type, entity_id, details, created_at)
    -- Migration 56 mistakenly used (actor_id, actor_role, action, resource, details)
    -- which caused every retention tick to roll back (column does not exist).
    IF NOT p_dry_run THEN
        INSERT INTO audit_logs (id, action, entity_type, entity_id, details, created_at)
        VALUES (
            gen_random_uuid()::text,
            'pii_retention_purge',
            'system',
            'retention_purge',
            v_result::text,
            v_started_at
        );
    END IF;

    RETURN v_result;
END;
$$;

REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role;

COMMENT ON FUNCTION purge_pii_retention(BOOLEAN) IS
    'B-P1-6 + DV-8 retention enforcement. Steps A–G per migrations 50/56. Migration 57 fixes audit_logs INSERT column list to match production schema (entity_type/entity_id). p_dry_run=true returns counts without mutating. See docs/runbooks/data-retention.md.';

NOTIFY pgrst, 'reload schema';
