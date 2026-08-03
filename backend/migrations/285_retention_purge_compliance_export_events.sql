-- 285_retention_purge_compliance_export_events.sql
-- ACTION_ITEMS.md D9 (gap G8 from
-- reports/audits/2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md).
--
-- Migration 263 created compliance_export_events with a table comment
-- claiming "7-year retention" and an unconditional BEFORE UPDATE OR DELETE
-- trigger blocking ALL mutation, including the retention delete that
-- comment promised. No background job enforced the claimed retention —
-- rows accumulate forever today. Long time horizon (first purge wouldn't be
-- due until 2033), so not urgent, but the claim currently overstates what
-- the system actually does.
--
-- Fix, mirroring the EXACT pattern migration 56 already established for
-- audit_logs (same problem: an append-only compliance/audit table that
-- still needs a retention exit door without opening it to everything else):
--
--   1. Replace the unconditional trigger function with one that still
--      blocks UPDATE unconditionally, but gates DELETE behind a
--      session-local flag (`spinr.compliance_export_events.allow_delete`)
--      instead of blocking it outright. Triggers see the same current_user
--      as whatever fired them — service_role included — so this is the
--      only way to authorise a SPECIFIC code path (the retention purge)
--      rather than a whole role.
--   2. Fork purge_pii_retention() verbatim from migration 228 (the current
--      authoritative definition) and add Step M: delete
--      compliance_export_events rows older than 7 years, setting the flag
--      immediately before the DELETE and clearing it immediately after
--      (including on exception), exactly mirroring Step G's audit_logs
--      handling. The existing retention_purge_loop picks this up with no
--      Python change — it already logs whatever keys purge_pii_retention()
--      returns.
--
-- Rollback:
--   Re-apply migration 228's purge_pii_retention definition verbatim (drops
--   Step M, the v_compliance_deleted counter, and the
--   c_compliance_export_age constant); and re-apply migration 263's
--   original _compliance_export_events_immutable() function (restores the
--   unconditional DELETE block).

-- ─────────────────────────────────────────────────────────────────────────
-- 1. Gate DELETE behind a session flag; UPDATE stays unconditionally blocked.
-- ─────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION _compliance_export_events_immutable()
RETURNS trigger LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS
$$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF current_setting('spinr.compliance_export_events.allow_delete', true) = 'true' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION
            'compliance_export_events rows are append-only and cannot be deleted directly — only purge_pii_retention() may delete rows past the 7-year retention window (row %)',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    RAISE EXCEPTION
        'compliance_export_events rows are append-only and cannot be updated';
END;
$$;

-- Trigger definition itself is unchanged (same name, same events) — only
-- the function body above changed, so no DROP/CREATE TRIGGER needed.

-- ─────────────────────────────────────────────────────────────────────────
-- 2. CREATE OR REPLACE purge_pii_retention() — Steps A-L copied verbatim
--    from migration 228 (append-only convention: this migration doesn't
--    edit 228, it re-forks its full body). Only Step M is new.
-- ─────────────────────────────────────────────────────────────────────────
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
    v_ai_msgs_deleted    INTEGER := 0;
    v_ai_convs_deleted   INTEGER := 0;
    v_surge_deleted      INTEGER := 0;
    v_ps_anonymized      INTEGER := 0;
    v_ps_deleted         INTEGER := 0;
    v_compliance_deleted INTEGER := 0;
    v_skipped_fk         INTEGER := 0;
    v_uid                TEXT;
    v_result             JSONB;

    c_gps_anon_age       INTERVAL := INTERVAL '3 years';
    c_ride_keep_age      INTERVAL := INTERVAL '7 years';
    c_loc_history_age    INTERVAL := INTERVAL '90 days';
    c_chat_age           INTERVAL := INTERVAL '90 days';
    c_token_grace_age    INTERVAL := INTERVAL '30 days';
    c_stripe_event_age   INTERVAL := INTERVAL '90 days';
    c_audit_log_age      INTERVAL := INTERVAL '7 years';
    c_surge_history_age  INTERVAL := INTERVAL '90 days';
    c_ps_anon_age        INTERVAL := INTERVAL '90 days';
    c_ps_keep_age        INTERVAL := INTERVAL '25 months';
    c_compliance_export_age INTERVAL := INTERVAL '7 years';
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
        WHERE received_at < v_started_at - c_loc_history_age;
        GET DIAGNOSTICS v_loc_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_loc_deleted
        FROM driver_location_history
        WHERE received_at < v_started_at - c_loc_history_age;
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

    -- Step H (migration 216): HARD-DELETE DSAR-deleted accounts whose full
    -- regulatory footprint has aged out.
    IF NOT p_dry_run THEN
        FOR v_uid IN
            SELECT u.id
            FROM users u
            WHERE u.deletion_scheduled_at IS NOT NULL
              AND u.deletion_scheduled_at <= v_started_at
              AND NOT EXISTS (SELECT 1 FROM rides r WHERE r.rider_id = u.id)
              AND NOT EXISTS (
                  SELECT 1 FROM drivers d
                  WHERE d.user_id = u.id
                    AND ( EXISTS (SELECT 1 FROM driver_insurance_periods dip WHERE dip.driver_id = d.id)
                       OR EXISTS (SELECT 1 FROM payouts p       WHERE p.driver_id = d.id)
                       OR EXISTS (SELECT 1 FROM bank_accounts b WHERE b.driver_id = d.id) )
              )
        LOOP
            BEGIN
                PERFORM 1 FROM users
                WHERE id = v_uid
                  AND deletion_scheduled_at IS NOT NULL
                  AND deletion_scheduled_at <= v_started_at
                FOR UPDATE;
                IF NOT FOUND THEN
                    CONTINUE;
                END IF;

                DELETE FROM financial_events         WHERE user_id = v_uid;
                DELETE FROM saved_addresses          WHERE user_id = v_uid;
                DELETE FROM support_tickets          WHERE user_id = v_uid;
                UPDATE reconciliation_discrepancies  SET resolved_by = NULL WHERE resolved_by = v_uid;
                DELETE FROM drivers                  WHERE user_id = v_uid;
                DELETE FROM users                    WHERE id = v_uid;

                v_dsar_purged := v_dsar_purged + 1;
            EXCEPTION WHEN foreign_key_violation THEN
                v_skipped_fk := v_skipped_fk + 1;
                RAISE WARNING 'purge_pii_retention Step H: skipped user % (residual FK) — %', v_uid, SQLERRM;
            END;
        END LOOP;
    ELSE
        SELECT COUNT(*) INTO v_dsar_purged
        FROM users u
        WHERE u.deletion_scheduled_at IS NOT NULL
          AND u.deletion_scheduled_at <= v_started_at
          AND NOT EXISTS (SELECT 1 FROM rides r WHERE r.rider_id = u.id)
          AND NOT EXISTS (
              SELECT 1 FROM drivers d
              WHERE d.user_id = u.id
                AND ( EXISTS (SELECT 1 FROM driver_insurance_periods dip WHERE dip.driver_id = d.id)
                   OR EXISTS (SELECT 1 FROM payouts p       WHERE p.driver_id = d.id)
                   OR EXISTS (SELECT 1 FROM bank_accounts b WHERE b.driver_id = d.id) )
          );
    END IF;

    -- Step I (117/129): clear ride_routes GPS geometry at 3y.
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

    -- Step J (141): delete AI assistant chat at 90d.
    IF NOT p_dry_run THEN
        DELETE FROM ai_messages
        WHERE created_at < v_started_at - c_chat_age;
        GET DIAGNOSTICS v_ai_msgs_deleted = ROW_COUNT;

        DELETE FROM ai_conversations c
        WHERE c.created_at < v_started_at - c_chat_age
          AND NOT EXISTS (SELECT 1 FROM ai_messages m WHERE m.conversation_id = c.id);
        GET DIAGNOSTICS v_ai_convs_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_ai_msgs_deleted
        FROM ai_messages
        WHERE created_at < v_started_at - c_chat_age;

        SELECT COUNT(*) INTO v_ai_convs_deleted
        FROM ai_conversations c
        WHERE c.created_at < v_started_at - c_chat_age
          AND NOT EXISTS (
              SELECT 1 FROM ai_messages m
              WHERE m.conversation_id = c.id
                AND m.created_at >= v_started_at - c_chat_age
          );
    END IF;

    -- Step K (143): delete surge_pricing history at 90d.
    IF NOT p_dry_run THEN
        DELETE FROM surge_pricing
        WHERE created_at < v_started_at - c_surge_history_age;
        GET DIAGNOSTICS v_surge_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_surge_deleted
        FROM surge_pricing
        WHERE created_at < v_started_at - c_surge_history_age;
    END IF;

    -- Step L (228): price_searches — anonymize user_id at 90d, delete rows
    -- at 25 months.
    IF NOT p_dry_run THEN
        UPDATE price_searches
        SET user_id = NULL
        WHERE created_at < v_started_at - c_ps_anon_age
          AND user_id IS NOT NULL;
        GET DIAGNOSTICS v_ps_anonymized = ROW_COUNT;

        DELETE FROM price_searches
        WHERE created_at < v_started_at - c_ps_keep_age;
        GET DIAGNOSTICS v_ps_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_ps_anonymized
        FROM price_searches
        WHERE created_at < v_started_at - c_ps_anon_age
          AND user_id IS NOT NULL;

        SELECT COUNT(*) INTO v_ps_deleted
        FROM price_searches
        WHERE created_at < v_started_at - c_ps_keep_age;
    END IF;

    -- Step M (migration 285, NEW): delete compliance_export_events at 7y
    -- (gated by session-flag, mirroring Step G's audit_logs handling —
    -- both are append-only compliance/audit logs with the same "only the
    -- retention purge may delete" requirement). Flag is set immediately
    -- before the DELETE and cleared immediately after, including on error,
    -- so a failure mid-purge can't leave the gate open for anything else
    -- running later in the same session.
    IF NOT p_dry_run THEN
        PERFORM set_config('spinr.compliance_export_events.allow_delete', 'true', true);
        BEGIN
            DELETE FROM compliance_export_events
            WHERE created_at < v_started_at - c_compliance_export_age;
            GET DIAGNOSTICS v_compliance_deleted = ROW_COUNT;
        EXCEPTION WHEN OTHERS THEN
            PERFORM set_config('spinr.compliance_export_events.allow_delete', 'false', true);
            RAISE;
        END;
        PERFORM set_config('spinr.compliance_export_events.allow_delete', 'false', true);
    ELSE
        SELECT COUNT(*) INTO v_compliance_deleted
        FROM compliance_export_events
        WHERE created_at < v_started_at - c_compliance_export_age;
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
        'dsar_users_skipped_fk',      v_skipped_fk,
        'ride_routes_anonymized',     v_routes_anonymized,
        'ai_messages_deleted',        v_ai_msgs_deleted,
        'ai_conversations_deleted',   v_ai_convs_deleted,
        'surge_pricing_deleted',      v_surge_deleted,
        'price_searches_anonymized',  v_ps_anonymized,
        'price_searches_deleted',     v_ps_deleted,
        'compliance_export_events_deleted', v_compliance_deleted
    );

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
    'B-P1-6 + B-P1-7 + B-P2-4 + DV-8 retention enforcement. Step C uses driver_location_history.received_at (migration 187). Step H (migration 216) HARD-DELETES DSAR-deleted accounts at 7y with NO anonymization once their regulatory footprint has cleared. Step I (117/129) clears ride_routes geometry at 3y; Step J (141) ai_messages/ai_conversations at 90d; Step K (143) surge_pricing at 90d; Step L (228) anonymizes price_searches.user_id at 90d and deletes rows at 25 months; Step M (285) deletes compliance_export_events at 7y (gated by spinr.compliance_export_events.allow_delete, mirroring Step G). p_dry_run=true returns counts without mutating.';

COMMENT ON TABLE compliance_export_events IS
    'Append-only audit log of admin-generated compliance/regulatory report '
    'exports (GST/PST remittance, insurance-period audit, DSAR lookup). '
    'INSERT only; UPDATE always blocked. DELETE blocked except inside '
    'purge_pii_retention() Step M (migration 285), which sets '
    'spinr.compliance_export_events.allow_delete=true for the 7y retention '
    'step. 7-year retention, now actually enforced (was claimed but not '
    'enforced from migration 263 through 284 — ACTION_ITEMS.md D9).';

NOTIFY pgrst, 'reload schema';
