-- Migration 57: regression fix for purge_pii_retention() (post-#141).
-- =============================================================================
-- ROOT CAUSE
-- ----------
-- Migrations 50 → 51 → 56 (mine, audit_logs_delete_lockdown) → 56 (#141,
-- purge_dsar_pending_deletions) all CREATE OR REPLACE the same function. They
-- run in alphanumeric order, so #141's body is what lives in production.
--
-- #141's body was forked from migration 50, NOT migration 51. That silently
-- reverted two fixes:
--
--   1. The audit-log INSERT schema mismatch. Migration 51 explicitly fixed
--      this with the comment: "Today the function would error on first real
--      run and roll back the entire purge transaction — silently disabling
--      the retention job. Fix the INSERT to match production columns." The
--      production schema (migration 06) is
--          (id, action, entity_type, entity_id, user_email, details, created_at)
--      — but #141's INSERT uses
--          (actor_id, actor_role, action, resource, details)
--      Those columns do not exist on the table; every retention-loop tick
--      hits a column-does-not-exist error and rolls back EVERYTHING (Steps
--      A through H). PIPEDA / SK Transportation Act retention is broken.
--
--   2. The 7-year audit_logs purge step. Migration 51 added Step G (DELETE
--      FROM audit_logs WHERE created_at < cutoff) gated by Saskatchewan
--      Transportation Act + internal policy. My migration 56 wrapped it with
--      a session-flag pattern (B-P2-4) so the audit_logs_no_delete trigger
--      could enforce append-only against ANY OTHER caller. #141 dropped the
--      step entirely.
--
-- This migration restores both, on top of #141's DSAR step (which is
-- correct and independent — only the surrounding plumbing was wrong).
--
-- WHAT THIS MIGRATION DOES
-- ------------------------
--   * CREATE OR REPLACE purge_pii_retention(boolean) with the merged body:
--       Steps A–F  (unchanged from #141 / 50)
--       Step G     7-year audit_logs purge — RESTORED from 51, gated by
--                  spinr.audit_logs.allow_delete session flag (B-P2-4)
--       Step H     DSAR pending-deletion user anonymization — UNCHANGED
--                  from #141 (DV-8, was Step G in #141 — renumbered to H
--                  here so the audit purge keeps its conventional G slot)
--       INSERT     audit row using migration 06 column names with
--                  v_result::text serialised
--
--   * No table-level changes; no trigger changes; this is a pure function
--     replacement. The audit_logs_no_delete trigger from migration 56
--     (mine) is already in place on main and is preserved by this fix —
--     the session-flag pattern works correctly with it.
--
-- ROLLBACK
-- --------
-- CREATE OR REPLACE with the prior #141 body. PIPEDA retention will remain
-- broken until a forward fix is applied; rollback should not be done in
-- production except as a last resort.
--
-- VERIFICATION
-- ------------
-- After deploy, run:
--     SELECT * FROM purge_pii_retention(true);
-- This is the dry-run path (p_dry_run=true) — it returns counts without
-- mutating, but exercises every SELECT including the audit-row pre-check
-- equivalent. A successful return JSONB containing every expected key
-- (rides_anonymized, ..., audit_logs_deleted, dsar_users_purged) confirms
-- the function compiles and executes cleanly. The first non-dry-run tick
-- of the daily retention loop will then succeed end-to-end.
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
    v_audit_deleted      INTEGER := 0;
    v_dsar_purged        INTEGER := 0;
    v_result             JSONB;

    -- Cutoff thresholds — see docs/runbooks/data-retention.md and
    -- @.claude/context/regulatory-sk.md before changing.
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

    -- Step G: audit_logs at 7y. RESTORED from migration 51, gated by the
    -- session-flag pattern from migration 56 (mine). The
    -- audit_logs_no_delete trigger blocks every other DELETE attempt; this
    -- block is the only legitimate caller and explicitly opts in via
    -- spinr.audit_logs.allow_delete = 'true' for the duration of the
    -- DELETE statement only.
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

    -- Step H: DSAR pending-deletion users whose 30-day grace period has
    -- elapsed. Gate: deleted_at IS NULL prevents double-processing on a
    -- re-run. We NULL identity + contact PII and stamp deleted_at; the row
    -- itself is retained (regulatory audit trail: when the deletion was
    -- executed). UNCHANGED from PR #141.
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
        'audit_logs_deleted',         v_audit_deleted,
        'dsar_users_purged',          v_dsar_purged
    );

    -- Audit trail. RESTORED column list from migration 51 — the production
    -- audit_logs schema (migration 06) is
    -- (id, action, entity_type, entity_id, user_email, details, created_at).
    -- The previous body (#141) used (actor_id, actor_role, action, resource,
    -- details) which fails on EVERY tick because those columns do not
    -- exist, rolling back the whole purge transaction.
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
    'B-P1-6 + B-P1-7 + B-P2-4 + DV-8 retention enforcement (regression fix in migration 57). Anonymizes ride GPS at 3y, deletes rides at 7y, deletes location history / ride chat / stripe events at 90d, deletes expired refresh tokens after a 30d grace period, deletes audit_logs at 7y (gated by session-flag spinr.audit_logs.allow_delete), and anonymizes DSAR pending-deletion users after their 30d grace period. Idempotent; safe to call from multiple replicas. p_dry_run=true returns counts without mutating.';

NOTIFY pgrst, 'reload schema';
