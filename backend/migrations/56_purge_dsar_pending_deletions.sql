-- Migration 56: extend purge_pii_retention() with DSAR pending-deletion user anonymization (DV-8).
-- =============================================================================
-- Problem: DELETE /account (R-P1-6) sets users.status='pending_deletion' and
--   records deletion_scheduled_at = now() + 30 days, but nothing ever executes
--   the actual PII wipe after the grace period expires. The daily retention loop
--   calls purge_pii_retention() which did not touch the users table at all.
--   Result: DSAR requests silently stall — a PIPEDA compliance gap.
--
-- Fix: add Step G to purge_pii_retention() via CREATE OR REPLACE (per the
--   migration-50 comment: "future adjustments go in a new migration that calls
--   CREATE OR REPLACE on this function"). Step G anonymizes PII on any user
--   whose deletion_scheduled_at has elapsed, marks them status='deleted', and
--   records a count in the return JSONB for observability.
--
-- What "anonymize" means here:
--   - first_name, last_name → NULL       (identity PII)
--   - email                → NULL        (contact PII)
--   - phone                → NULL        (contact PII)
--   - avatar_url           → NULL        (biometric-adjacent PII)
--   - status               → 'deleted'
--   - deleted_at           → now()       (idempotency gate: NULL check)
--   Ride rows are NOT touched — they stay for regulatory retention (7 y) and
--   are already user_id-linked for financial audit. The GPS in those rows is
--   scrubbed separately by Step A at the 3-year mark.
--
-- Rollback plan: CREATE OR REPLACE with a version of the function that omits
--   Step G. No data is un-deletable (rows were already pending deletion by
--   user consent); rolling back only prevents future executions of Step G.
--   Already-anonymized rows are not recoverable — that is the intended outcome.
--
-- Idempotency: WHERE deleted_at IS NULL ensures a row is only processed once.
--   A second run finds zero candidates and returns dsar_users_purged=0.
-- =============================================================================

-- Index for the new WHERE clause — deletion_scheduled_at + status filter.
-- Partial index covers only the small pending_deletion population; once a row
-- transitions to 'deleted' it leaves the index entirely.
CREATE INDEX IF NOT EXISTS idx_users_pending_deletion
    ON users (deletion_scheduled_at)
    WHERE status = 'pending_deletion' AND deleted_at IS NULL;

-- Extend the daily purge function with Step G.
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

    -- Cutoff thresholds (unchanged from migration 50).
    c_gps_anon_age       INTERVAL := INTERVAL '3 years';
    c_ride_keep_age      INTERVAL := INTERVAL '7 years';
    c_loc_history_age    INTERVAL := INTERVAL '90 days';
    c_chat_age           INTERVAL := INTERVAL '90 days';
    c_token_grace_age    INTERVAL := INTERVAL '30 days';
    c_stripe_event_age   INTERVAL := INTERVAL '90 days';
BEGIN
    -- Step A: anonymize ride GPS at 3 years (unchanged).
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

    -- Step B: hard-delete rides at 7 years (unchanged).
    IF NOT p_dry_run THEN
        DELETE FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
        GET DIAGNOSTICS v_rides_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_rides_deleted
        FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
    END IF;

    -- Step C: driver location history at 90 days (unchanged).
    IF NOT p_dry_run THEN
        DELETE FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
        GET DIAGNOSTICS v_loc_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_loc_deleted
        FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
    END IF;

    -- Step D: ride chat at 90 days (unchanged).
    IF NOT p_dry_run THEN
        DELETE FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
        GET DIAGNOSTICS v_msgs_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_msgs_deleted
        FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
    END IF;

    -- Step E: expired refresh tokens (unchanged).
    IF NOT p_dry_run THEN
        DELETE FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
        GET DIAGNOSTICS v_tokens_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_tokens_deleted
        FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
    END IF;

    -- Step F: Stripe webhook idempotency rows at 90 days (unchanged).
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
    --   Gate: deleted_at IS NULL prevents double-processing on a re-run.
    --   We NULL identity + contact PII and stamp deleted_at; the row itself is
    --   retained (regulatory audit trail: when the deletion was executed).
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

    IF NOT p_dry_run THEN
        INSERT INTO audit_logs (actor_id, actor_role, action, resource, details)
        VALUES (
            'system:retention_purge',
            'system',
            'pii_retention_purge',
            'system',
            v_result
        );
    END IF;

    RETURN v_result;
END;
$$;

-- Permissions unchanged from migration 50.
REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role;

COMMENT ON FUNCTION purge_pii_retention(BOOLEAN) IS
    'B-P1-6 + DV-8 retention enforcement. Steps A–F unchanged from migration 50. Step G (added migration 56): anonymizes PII for users in pending_deletion status once their 30-day DSAR grace period elapses (PIPEDA right-to-erasure). p_dry_run=true returns counts without mutating. See docs/runbooks/data-retention.md.';

NOTIFY pgrst, 'reload schema';
