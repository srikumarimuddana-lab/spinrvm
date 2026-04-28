-- Migration 56: audit_logs append-only DELETE lockdown (B-P2-4 closure).
-- =============================================================================
-- Migration 51 closed UPDATE on audit_logs (audit_logs_no_update trigger). It
-- deliberately left DELETE open so purge_pii_retention() could enforce the 7y
-- retention ceiling. That decision left a residual risk: any code path running
-- as service_role (env-var compromise, server-side bug that calls
-- delete_many('audit_logs', ...)) can quietly erase forensic evidence.
--
-- This migration closes the gap with a session-flag pattern that gates DELETE:
--
--   1. A BEFORE DELETE trigger on audit_logs raises unless the
--      session-local flag `spinr.audit_logs.allow_delete` is set to 'true'.
--      Triggers fire for service_role too — there is no SECURITY DEFINER
--      bypass and no role check that can be sidestepped from a stolen JWT.
--
--   2. purge_pii_retention() sets the flag (transaction-local) before its
--      DELETE FROM audit_logs and clears it after, so the legitimate retention
--      step still works. Other DELETE attempts — from admin routes, ad-hoc
--      DBA queries via the REST API, or a future code bug — fail loudly with
--      a check_violation error.
--
-- Why a session flag instead of role checks: Postgres triggers see the same
-- current_user as the SQL that fired them, so service_role queries are
-- indistinguishable from the legitimate retention path. The flag-based
-- pattern is the only way to authorise a SPECIFIC code path rather than a
-- WHOLE role.
--
-- Reversibility: drop the trigger + function; restore the prior CREATE OR
-- REPLACE of purge_pii_retention from migration 51.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. BEFORE DELETE trigger function. Raises check_violation unless the
--    transaction has set spinr.audit_logs.allow_delete = 'true'.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION audit_logs_block_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_allowed TEXT;
BEGIN
    -- current_setting(name, missing_ok=true) returns NULL when the GUC
    -- has never been set, '' when set to empty, or the actual value.
    v_allowed := current_setting('spinr.audit_logs.allow_delete', true);
    IF v_allowed IS NULL OR v_allowed <> 'true' THEN
        RAISE EXCEPTION
            'audit_logs DELETE is reserved for purge_pii_retention() — direct DELETE is not permitted (attempted on row %)',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs;
CREATE TRIGGER audit_logs_no_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW
    EXECUTE FUNCTION audit_logs_block_delete();

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. CREATE OR REPLACE purge_pii_retention() to set the session flag around
--    the audit_logs DELETE. Only Step G changes; every other step (A-F) is
--    copied verbatim from migration 51 to satisfy the append-only convention
--    (we don't edit migration 51 in place).
-- ─────────────────────────────────────────────────────────────────────────────
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
        FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
    END IF;

    -- Step C: driver_location_history at 90d.
    IF NOT p_dry_run THEN
        DELETE FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
        GET DIAGNOSTICS v_loc_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_loc_deleted
        FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
    END IF;

    -- Step D: ride_messages at 90d.
    IF NOT p_dry_run THEN
        DELETE FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
        GET DIAGNOSTICS v_msgs_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_msgs_deleted
        FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
    END IF;

    -- Step E: refresh_tokens at expires_at + 30d.
    IF NOT p_dry_run THEN
        DELETE FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
        GET DIAGNOSTICS v_tokens_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_tokens_deleted
        FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
    END IF;

    -- Step F: stripe_events at 90d.
    IF NOT p_dry_run THEN
        DELETE FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
        GET DIAGNOSTICS v_stripe_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_stripe_deleted
        FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
    END IF;

    -- Step G: audit_logs at 7y.
    -- B-P2-4: set the session-local allow-delete flag immediately before
    -- the DELETE and clear it immediately after. Any other code path that
    -- tries to DELETE FROM audit_logs without setting this flag will hit
    -- the audit_logs_no_delete trigger and raise check_violation.
    IF NOT p_dry_run THEN
        PERFORM set_config('spinr.audit_logs.allow_delete', 'true', true);
        BEGIN
            DELETE FROM audit_logs
            WHERE created_at < v_started_at - c_audit_log_age;
            GET DIAGNOSTICS v_audit_deleted = ROW_COUNT;
        EXCEPTION WHEN OTHERS THEN
            -- Always clear the flag on the way out, even on error.
            PERFORM set_config('spinr.audit_logs.allow_delete', 'false', true);
            RAISE;
        END;
        PERFORM set_config('spinr.audit_logs.allow_delete', 'false', true);
    ELSE
        SELECT COUNT(*) INTO v_audit_deleted
        FROM audit_logs
        WHERE created_at < v_started_at - c_audit_log_age;
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
        'audit_logs_deleted',         v_audit_deleted
    );

    -- Audit trail.
    IF NOT p_dry_run THEN
        INSERT INTO audit_logs (id, action, entity_type, entity_id, user_email, details, created_at)
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
    'B-P1-6 + B-P1-7 + B-P2-4 retention enforcement. Anonymizes ride GPS at 3y, deletes rides at 7y, deletes location history / ride chat / stripe events at 90d, deletes expired refresh tokens after a 30d grace period, deletes audit_logs at 7y (gated by session-flag spinr.audit_logs.allow_delete). Idempotent; safe to call from multiple replicas. p_dry_run=true returns counts without mutating.';

COMMENT ON TABLE audit_logs IS
    'Append-only forensic log. UPDATE blocked by trigger audit_logs_no_update; DELETE blocked by trigger audit_logs_no_delete except inside purge_pii_retention() which sets spinr.audit_logs.allow_delete=true for the 7y retention step. INSERT goes through service_role only (backend); SELECT goes through authenticated admins (RLS) or service_role.';

NOTIFY pgrst, 'reload schema';
