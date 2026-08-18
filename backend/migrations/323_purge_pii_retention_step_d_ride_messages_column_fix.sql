-- 323_purge_pii_retention_step_d_ride_messages_column_fix.sql
--
-- BUGFIX: purge_pii_retention() Step D referenced ride_messages.created_at,
-- but that column does not exist on production. Migration 98's table
-- definition gave ride_messages a `timestamp` column, not `created_at`.
-- Migration 50 (the original purge_pii_retention) assumed the table might
-- not exist yet and its own idempotent "IF NOT EXISTS" table-creation
-- statement for ride_messages (... created_at ...) was a no-op against the
-- already-existing table (98 ran first) -- so `created_at` was never
-- actually added, and Step D's WHERE clause has referenced a nonexistent
-- column since day one. Postgres does not validate column references
-- inside a plpgsql function body at definition time, only at execution, so
-- this was invisible until the function actually ran Step D -- confirmed
-- live 2026-08-17 via a purge_pii_retention(true) dry-run call, immediately
-- after applying migration 321 (A38):
--     ERROR: 42703: column "created_at" does not exist
--     QUERY: SELECT COUNT(*) FROM ride_messages WHERE created_at < ...
--     CONTEXT: PL/pgSQL function purge_pii_retention(boolean) line 88
--
-- This is the EXACT same bug class migration 187 already found and fixed for
-- driver_location_history's Step C (`recorded_at` vs `received_at`) -- same
-- root cause (a table pre-existing under a different column name than a
-- later idempotent table-creation statement assumed), same failure mode (every daily
-- tick errors out at the first bad column reference and aborts every step
-- after it), same fix shape (re-fork verbatim, change only the one broken
-- column reference).
--
-- Impact: EVERY step after Step D in purge_pii_retention() -- Step E onward
-- (refresh_tokens, stripe_events, audit_logs, Step H's DSAR account
-- hard-delete, Step I ride-route GPS clearing, Step J AI chat, Step K surge
-- history, Step L price_searches, Step M compliance_export_events, Step N
-- profile-scrub) -- has never run to completion in this daily background
-- loop (utils/retention_purge.py, ~03:00 UTC). Steps A-C (ride GPS
-- anonymization at 3y, ride hard-delete at 7y, driver_location_history at
-- 90d -- the last already fixed by migration 187) DO complete, since they
-- run before Step D in function body order and each step commits its own
-- statement (no single enclosing transaction inside the plpgsql function
-- body wraps the whole thing -- an unhandled exception at Step D aborts the
-- CURRENT transaction, which for a single top-level RPC call is the entire
-- function invocation; each step's own statement runs and its effects are
-- visible up to the point of the error, but the function itself raises and
-- the RPC call fails, so utils/retention_purge.py's caller sees a hard
-- error and logs it via `logger.exception` every single tick -- not a
-- successful run of Steps A-C either, since a raised exception inside a
-- plpgsql function rolls back the ENTIRE function's work by default,
-- Postgres functions are not autonomous per-statement transactions).
--
-- FIX: re-fork purge_pii_retention VERBATIM from migration 321 (the current,
-- just-applied, correct A38 version) and change ONLY Step D's ride_messages
-- column reference from `created_at` to `timestamp`. Nothing else in the
-- function changes -- Step H's A38 driver-ride guard (migration 321) is
-- preserved unchanged.
--
-- Also adds the index Step D's filter needs, which migration 50's own
-- `idx_ride_messages_created_at` (also on the nonexistent `created_at`
-- column) never actually landed for the same reason -- confirmed live via
-- pg_indexes: only `ride_messages_pkey` and `idx_ride_messages_ride_timestamp`
-- (ride_id, timestamp) exist, no standalone index on `timestamp` alone for
-- an unscoped age filter.
--
-- Forward-compatible: CREATE OR REPLACE FUNCTION + CREATE INDEX IF NOT
-- EXISTS, no data mutation, no lock beyond what the existing 03:00 UTC loop
-- already takes under its Redis leader lock. First successful run will
-- clear the backlog of every step's overdue rows in one pass.
--
-- Rollback:
--   Re-apply migration 321's purge_pii_retention definition verbatim
--   (reverts Step D's `timestamp` reference back to the broken `created_at`).
--   DROP INDEX IF EXISTS idx_ride_messages_timestamp_purge; is safe either
--   way -- it only ever speeds up a query, never changes behavior.

CREATE INDEX IF NOT EXISTS idx_ride_messages_timestamp_purge
    ON ride_messages ("timestamp");

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
    v_profile_scrubbed   INTEGER := 0;
    v_addr_deleted       INTEGER := 0;
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
    c_profile_scrub_age  INTERVAL := INTERVAL '30 days';
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

    -- Step B: hard-delete rides at 7y. financial_events.ride_id is
    -- ON DELETE SET NULL (migration 294/295) so this no longer aborts on
    -- a paid ride's retained ledger row.
    IF NOT p_dry_run THEN
        DELETE FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
        GET DIAGNOSTICS v_rides_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_rides_deleted
        FROM rides WHERE created_at < v_started_at - c_ride_keep_age;
    END IF;

    -- Step C (187): delete driver_location_history at 90d.
    -- `received_at`, not `recorded_at` -- see migration 187's own header.
    IF NOT p_dry_run THEN
        DELETE FROM driver_location_history
        WHERE received_at < v_started_at - c_loc_history_age;
        GET DIAGNOSTICS v_loc_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_loc_deleted
        FROM driver_location_history
        WHERE received_at < v_started_at - c_loc_history_age;
    END IF;

    -- Step D (323): delete ride_messages at 90d.
    -- `timestamp`, not `created_at` -- see this migration's own header.
    IF NOT p_dry_run THEN
        DELETE FROM ride_messages
        WHERE "timestamp" < v_started_at - c_chat_age;
        GET DIAGNOSTICS v_msgs_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_msgs_deleted
        FROM ride_messages
        WHERE "timestamp" < v_started_at - c_chat_age;
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

    -- Step H (migration 216, driver-ride guard added by A38/migration 321):
    -- HARD-DELETE DSAR-deleted accounts whose full regulatory footprint has
    -- aged out. The driver-side guard checks `rides.driver_id` in addition
    -- to driver_insurance_periods/payouts/bank_accounts (A38).
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
                       OR EXISTS (SELECT 1 FROM bank_accounts b WHERE b.driver_id = d.id)
                       OR EXISTS (SELECT 1 FROM rides r2        WHERE r2.driver_id = d.id) )
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

                PERFORM set_config('spinr.financial_events.allow_delete', 'true', true);
                BEGIN
                    DELETE FROM financial_events     WHERE user_id = v_uid;
                EXCEPTION WHEN OTHERS THEN
                    PERFORM set_config('spinr.financial_events.allow_delete', 'false', true);
                    RAISE;
                END;
                PERFORM set_config('spinr.financial_events.allow_delete', 'false', true);

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
                   OR EXISTS (SELECT 1 FROM bank_accounts b WHERE b.driver_id = d.id)
                   OR EXISTS (SELECT 1 FROM rides r2        WHERE r2.driver_id = d.id) )
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

    -- Step M (285): delete compliance_export_events at 7y (gated).
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

    -- Step N (296, B18): scrub profile PII 30 days after a DSAR deletion
    -- request, per regulatory-sk.md's Right-to-delete #1. Independent of
    -- Step H's 7-year hard delete -- this closes the gap where profile
    -- fields stayed fully live for the whole 7-year window. Re-reads
    -- status='pending_deletion' at execution time so a reactivated account
    -- (status flipped back on login) is naturally excluded -- same TOCTOU
    -- shape as Step H's own recheck, no separate FOR UPDATE needed since
    -- this only nulls columns, it doesn't delete the row.
    IF NOT p_dry_run THEN
        WITH scrubbed AS (
            UPDATE users
            SET first_name          = NULL,
                last_name           = NULL,
                email               = NULL,
                profile_image       = NULL,
                profile_scrubbed_at = v_started_at
            WHERE status = 'pending_deletion'
              AND deletion_requested_at IS NOT NULL
              AND deletion_requested_at < v_started_at - c_profile_scrub_age
              AND profile_scrubbed_at IS NULL
            RETURNING id
        )
        DELETE FROM saved_addresses
        WHERE user_id IN (SELECT id FROM scrubbed);
        GET DIAGNOSTICS v_addr_deleted = ROW_COUNT;

        SELECT COUNT(*) INTO v_profile_scrubbed
        FROM users
        WHERE profile_scrubbed_at = v_started_at;
    ELSE
        SELECT COUNT(*) INTO v_profile_scrubbed
        FROM users
        WHERE status = 'pending_deletion'
          AND deletion_requested_at IS NOT NULL
          AND deletion_requested_at < v_started_at - c_profile_scrub_age
          AND profile_scrubbed_at IS NULL;

        SELECT COUNT(*) INTO v_addr_deleted
        FROM saved_addresses
        WHERE user_id IN (
            SELECT id FROM users
            WHERE status = 'pending_deletion'
              AND deletion_requested_at IS NOT NULL
              AND deletion_requested_at < v_started_at - c_profile_scrub_age
              AND profile_scrubbed_at IS NULL
        );
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
        'compliance_export_events_deleted', v_compliance_deleted,
        'profiles_scrubbed',          v_profile_scrubbed,
        'saved_addresses_deleted_on_scrub', v_addr_deleted
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
    'B-P1-6 + B-P1-7 + B-P2-4 + DV-8 + B17 + B18 + A38 + Step-D-column-fix (323) retention enforcement. Step B (7y rides) relies on financial_events_ride_id_fkey ON DELETE SET NULL (294/295). Step C uses driver_location_history.received_at (187). Step D uses ride_messages.timestamp, not created_at (323). Step H (216, driver-ride guard added by A38/321) HARD-DELETES DSAR-deleted accounts at 7y with NO anonymization once their regulatory footprint (rides as rider OR driver, driver_insurance_periods, payouts, bank_accounts) has cleared. Step N (296) scrubs profile PII (name/email/profile_image/saved_addresses) 30 days after a DSAR deletion request, independent of Step H''s 7y window, per regulatory-sk.md Right-to-delete #1. p_dry_run=true returns counts without mutating.';

NOTIFY pgrst, 'reload schema';
