-- Migration 51: audit_logs lockdown + 7y retention (B-P1-7).
-- =============================================================================
-- Closes audit B-P1-7. Three intertwined fixes on the same table, shipped as
-- one migration because they all touch audit_logs:
--
--   1. RLS lockdown. Migration 06 created `Admin full access audit_logs` as
--      FOR ALL TO authenticated. That permits an authenticated admin JWT
--      (via PostgREST) to UPDATE or DELETE audit rows, which silently breaks
--      the append-only contract and would let an attacker with a stolen
--      admin token cover their tracks. Replace with FOR SELECT only, plus a
--      BEFORE-UPDATE trigger that blocks UPDATE for every role including
--      service_role (the trigger fires regardless of BYPASSRLS — defense
--      against a future backend bug that calls update_one("audit_logs", ...)).
--
--   2. PostgREST surface narrowing. anon should never see audit_logs at all;
--      authenticated should have SELECT only (still gated by the admin RLS
--      policy). Backend writes go through service_role and bypass both.
--
--   3. Schema-mismatch bug fix in purge_pii_retention(). Migration 50's
--      audit-log INSERT references columns from migration 08's never-applied
--      schema (actor_id, actor_role, resource). The real production schema
--      is migration 06's (action, entity_type, entity_id, user_email,
--      details TEXT). Today the function would error on first real run and
--      roll back the entire purge transaction — silently disabling the
--      retention job. Fix the INSERT to match production columns.
--
--   4. 7-year retention step on audit_logs. Saskatchewan Transportation Act
--      + internal policy retain audit history for 7y for regulatory
--      inspection. Beyond that, action history is no longer needed and
--      should be purged (PIPEDA "no longer needed for stated purpose").
--      Add Step G to the existing daily purge function.
--
-- Append-only convention: this is a new migration (51) that calls
-- CREATE OR REPLACE on the function from migration 50. Migration 50 is not
-- edited.
--
-- Rollback plan: drop the trigger, restore the original FOR ALL policy,
-- revert the function via another CREATE OR REPLACE. The 7y DELETE in
-- Step G is irreversible short of PITR — same posture as the other
-- retention steps.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Tighten the existing admin policy: SELECT only, no UPDATE/DELETE/INSERT.
--    Inserts come exclusively from backend code via service_role; no
--    legitimate admin path needs to write audit rows directly.
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "Admin full access audit_logs" ON audit_logs;

CREATE POLICY "Admin read audit_logs"
    ON audit_logs FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = auth.uid()::text
              AND users.role IN ('admin', 'super_admin')
        )
    );

-- Service-role bypass policy from migration 06 stays in place (the backend
-- needs INSERT for log_audit() / ride decline / retention purge, and DELETE
-- for the 7y retention step below).

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Append-only enforcement. RLS without an UPDATE policy already denies
--    UPDATE for anon/authenticated; this trigger extends the block to
--    service_role too. The only legitimate mutations on audit_logs are
--    INSERT (write a new row) and DELETE (retention purge); UPDATE has no
--    valid reason. A backend bug that issued an UPDATE today would corrupt
--    forensic history silently — the trigger turns that into a loud error.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION audit_logs_block_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'audit_logs is append-only — UPDATE is not permitted (attempted on row %)',
        OLD.id
        USING ERRCODE = 'check_violation';
END;
$$;

DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs;
CREATE TRIGGER audit_logs_no_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW
    EXECUTE FUNCTION audit_logs_block_update();

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. PostgREST surface narrowing. anon never sees audit_logs; authenticated
--    can SELECT (gated by RLS) but cannot INSERT/UPDATE/DELETE through the
--    REST API. Backend uses service_role and bypasses both.
-- ─────────────────────────────────────────────────────────────────────────────
REVOKE ALL ON audit_logs FROM anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON audit_logs FROM authenticated;
GRANT  SELECT ON audit_logs TO authenticated;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Index for the 7y retention scan. created_at already has an index from
--    migration 06 (idx_audit_logs_created DESC); a DESC index is fine for
--    `created_at < cutoff` range scans because Postgres reads it in either
--    direction. No additional index needed.
-- ─────────────────────────────────────────────────────────────────────────────

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. CREATE OR REPLACE purge_pii_retention(): fix the broken INSERT and add
--    Step G (audit_logs 7y purge). Constants and other steps unchanged from
--    migration 50.
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
    -- Step A: anonymize ride GPS at 3y (unchanged from migration 50).
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

    -- Step B: hard-delete rides at 7y (unchanged from migration 50).
    IF NOT p_dry_run THEN
        DELETE FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
        GET DIAGNOSTICS v_rides_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_rides_deleted
        FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
    END IF;

    -- Step C: driver_location_history at 90d (unchanged).
    IF NOT p_dry_run THEN
        DELETE FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
        GET DIAGNOSTICS v_loc_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_loc_deleted
        FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
    END IF;

    -- Step D: ride_messages at 90d (unchanged).
    IF NOT p_dry_run THEN
        DELETE FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
        GET DIAGNOSTICS v_msgs_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_msgs_deleted
        FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
    END IF;

    -- Step E: refresh_tokens at expires_at + 30d (unchanged).
    IF NOT p_dry_run THEN
        DELETE FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
        GET DIAGNOSTICS v_tokens_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_tokens_deleted
        FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
    END IF;

    -- Step F: stripe_events at 90d (unchanged).
    IF NOT p_dry_run THEN
        DELETE FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
        GET DIAGNOSTICS v_stripe_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_stripe_deleted
        FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
    END IF;

    -- Step G (NEW): audit_logs at 7y. Saskatchewan Transportation Act +
    -- internal policy ceiling. Beyond 7y the action history has no
    -- regulatory or operational use; PIPEDA "no longer needed" applies.
    -- This step is intentionally placed before the function's own audit-log
    -- INSERT below so today's run row is never deleted by today's run.
    IF NOT p_dry_run THEN
        DELETE FROM audit_logs
        WHERE created_at < v_started_at - c_audit_log_age;
        GET DIAGNOSTICS v_audit_deleted = ROW_COUNT;
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

    -- Audit trail. The original migration 50 INSERT used columns from a
    -- never-applied schema (actor_id/actor_role/resource); fix to match the
    -- production schema (action/entity_type/entity_id/user_email/details
    -- TEXT). v_started_at::text doubles as a stable per-run identifier.
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

-- Lockdown unchanged from migration 50; re-stated for clarity.
REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role;

COMMENT ON FUNCTION purge_pii_retention(BOOLEAN) IS
    'B-P1-6 + B-P1-7 retention enforcement. Anonymizes ride GPS at 3y, deletes rides at 7y, deletes location history / ride chat / stripe events at 90d, deletes expired refresh tokens after a 30d grace period, deletes audit_logs at 7y. Idempotent; safe to call from multiple replicas. p_dry_run=true returns counts without mutating. See docs/runbooks/data-retention.md.';

COMMENT ON TABLE audit_logs IS
    'Append-only forensic log of admin actions, ride declines, and system events. UPDATE is blocked by trigger audit_logs_no_update. INSERT goes through service_role only (backend); SELECT goes through authenticated admins (RLS) or service_role. DELETE is reserved for the 7y retention step in purge_pii_retention().';

NOTIFY pgrst, 'reload schema';
