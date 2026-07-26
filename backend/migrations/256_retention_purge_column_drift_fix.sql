-- 256_retention_purge_column_drift_fix.sql
--
-- BUGFIX (Sentry: "retention_purge: rpc(purge_pii_retention) failed" →
--   DatabaseError / APIError 42703: column "created_at" does not exist):
-- purge_pii_retention() aborts mid-run, so the ENTIRE daily retention purge
-- rolls back — no step commits, including the ones that come before the
-- failure. Nothing has been purged since the regression landed.
--
-- Two column references drifted away from the live schema:
--
--   Step F — stripe_events.created_at
--     That column has never existed. Migration 22's CREATE TABLE defines
--     `received_at` (+ `processed_at`), migration 50 added
--     idx_stripe_events_received_at specifically for this purge step, and
--     migrations 50/51/56/57 all correctly used `received_at`. Migration 67
--     (the actor_id fix) re-forked the function and silently rewrote Step F's
--     two references to `created_at`; every re-fork since (117, 129, 141, 143,
--     187, 216, 228) carried the regression forward verbatim.
--
--   Step D — ride_messages.created_at
--     Ambiguous in the migration tree, which is itself the problem. Migrations
--     08 and 50 declare a `created_at` shape; migration 98 — which states it is
--     "the authoritative create" — declares `timestamp`, and that is the shape
--     the live chat path uses (routes/rides/chat.py writes text/sender/timestamp
--     and ORDER BYs `timestamp`). Both CREATEs are IF NOT EXISTS, so which one
--     won depends on production history we cannot read from the tree.
--
-- This is the third recurrence of the same class of bug: migration 187 fixed
-- Step C after it referenced driver_location_history.recorded_at, a column that
-- also only ever existed in a CREATE TABLE IF NOT EXISTS that production
-- no-op'd. 187's fix delivered nothing, because Step F still aborted the
-- transaction a few statements later.
--
-- FIX: re-fork purge_pii_retention VERBATIM from migration 228 and change ONLY
-- Steps D and F, which now resolve their retention timestamp column against the
-- LIVE catalog (pg_attribute) instead of hard-coding a name the tree cannot
-- vouch for:
--
--   • Candidates are an explicit, ordered preference list — `received_at` then
--     `created_at` for stripe_events, `timestamp` then `created_at` for
--     ride_messages. No wildcard "find a timestamp column" guessing.
--   • If the table is missing, `::regclass` raises. If none of the candidates
--     exist, we RAISE EXCEPTION. Drift fails loudly and the purge still aborts
--     — it is never silently skipped (CLAUDE.md: "do not silently swallow
--     errors").
--   • The resolved names are reported in the result JSONB under
--     `retention_ts_columns`, so the daily audit_logs row and the loop's INFO
--     log record which columns each run actually used. A future drift is
--     visible in the audit trail instead of being inferred from a stack trace.
--
-- Identifier interpolation uses format('%I') over names read from pg_catalog
-- (never from a caller argument), so the dynamic statements carry no injection
-- surface. p_dry_run remains the only parameter.
--
-- Forward-compatible: CREATE OR REPLACE FUNCTION, no schema change, no new
-- locks. The first successful run deletes the accumulated >90d backlog of
-- ride_messages and stripe_events in one DELETE each, then Steps G-L run for
-- the first time ever on this definition.
--
-- Rollback:
--   Re-apply migration 228's purge_pii_retention definition verbatim (restores
--   the hard-coded `created_at` in Steps D and F, the v_msgs_ts_col /
--   v_stripe_ts_col resolution block, and the 'retention_ts_columns' key).
--   Note this reinstates the failure this migration fixes.

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
    v_skipped_fk         INTEGER := 0;
    v_uid                TEXT;
    v_msgs_ts_col        TEXT;
    v_stripe_ts_col      TEXT;
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
BEGIN
    -- Step 0 (migration 256): resolve the two drifted retention timestamp
    -- columns against the live catalog. pg_attribute (not information_schema)
    -- so the lookup is not filtered by column privileges. A missing table
    -- raises on ::regclass; a table with none of the candidate columns raises
    -- below. Either way the purge aborts loudly rather than skipping a step.
    SELECT a.attname::text INTO v_msgs_ts_col
    FROM pg_catalog.pg_attribute a
    WHERE a.attrelid = 'public.ride_messages'::regclass
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND a.attname IN ('timestamp', 'created_at')
    ORDER BY CASE a.attname WHEN 'timestamp' THEN 1 ELSE 2 END
    LIMIT 1;

    IF v_msgs_ts_col IS NULL THEN
        RAISE EXCEPTION
            'purge_pii_retention Step D: ride_messages has neither "timestamp" nor "created_at" — chat retention (90d) cannot be enforced';
    END IF;

    SELECT a.attname::text INTO v_stripe_ts_col
    FROM pg_catalog.pg_attribute a
    WHERE a.attrelid = 'public.stripe_events'::regclass
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND a.attname IN ('received_at', 'created_at')
    ORDER BY CASE a.attname WHEN 'received_at' THEN 1 ELSE 2 END
    LIMIT 1;

    IF v_stripe_ts_col IS NULL THEN
        RAISE EXCEPTION
            'purge_pii_retention Step F: stripe_events has neither "received_at" nor "created_at" — webhook-dedup retention (90d) cannot be enforced';
    END IF;

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
    -- (migration 187 fix: keyed on received_at — the server-receipt column.)
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
    -- (migration 256 fix: keyed on the column resolved in Step 0 — `timestamp`
    -- on the migration-98 shape the live chat path writes, `created_at` on the
    -- legacy 08/50 shape. Was hard-coded to `created_at`, which 42703'd.)
    IF NOT p_dry_run THEN
        EXECUTE format('DELETE FROM ride_messages WHERE %I < $1', v_msgs_ts_col)
            USING v_started_at - c_chat_age;
        GET DIAGNOSTICS v_msgs_deleted = ROW_COUNT;
    ELSE
        EXECUTE format('SELECT COUNT(*) FROM ride_messages WHERE %I < $1', v_msgs_ts_col)
            INTO v_msgs_deleted
            USING v_started_at - c_chat_age;
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
    -- (migration 256 fix: keyed on the column resolved in Step 0 — `received_at`
    -- per migration 22's CREATE TABLE and migration 50's index. Migration 67
    -- rewrote this to `created_at`, a column that has never existed, and every
    -- run since aborted here with 42703 — rolling back the whole purge.)
    IF NOT p_dry_run THEN
        EXECUTE format('DELETE FROM stripe_events WHERE %I < $1', v_stripe_ts_col)
            USING v_started_at - c_stripe_event_age;
        GET DIAGNOSTICS v_stripe_deleted = ROW_COUNT;
    ELSE
        EXECUTE format('SELECT COUNT(*) FROM stripe_events WHERE %I < $1', v_stripe_ts_col)
            INTO v_stripe_deleted
            USING v_started_at - c_stripe_event_age;
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

    -- Step H (CHANGED, migration 216): HARD-DELETE DSAR-deleted accounts whose
    -- FULL regulatory footprint has aged out — NO anonymization (Uber/Lyft
    -- "tombstone until aged out"). Eligibility: window elapsed, no rides remain
    -- (Step B), and — for drivers — no driver_insurance_periods / payouts /
    -- bank_accounts (we NEVER touch the append-only insurance-period table, so a
    -- real driver stays tombstoned indefinitely). Per-account subtransaction +
    -- FK EXCEPTION handler ⇒ an unexpected RESTRICT dependency skips that account
    -- (logged), never aborting the purge; FOR UPDATE re-check closes the
    -- reactivation TOCTOU.
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
                -- Re-verify under a row lock: skip if reactivated since the scan.
                PERFORM 1 FROM users
                WHERE id = v_uid
                  AND deletion_scheduled_at IS NOT NULL
                  AND deletion_scheduled_at <= v_started_at
                FOR UPDATE;
                IF NOT FOUND THEN
                    CONTINUE;
                END IF;

                -- Clear deletable dependents (all >= 7y old — account locked >= 7y).
                DELETE FROM financial_events         WHERE user_id = v_uid;
                DELETE FROM saved_addresses          WHERE user_id = v_uid;
                DELETE FROM support_tickets          WHERE user_id = v_uid;
                UPDATE reconciliation_discrepancies  SET resolved_by = NULL WHERE resolved_by = v_uid;
                -- Driver row is guaranteed free of insurance/payout/bank above.
                DELETE FROM drivers                  WHERE user_id = v_uid;
                DELETE FROM users                    WHERE id = v_uid;

                v_dsar_purged := v_dsar_purged + 1;
            EXCEPTION WHEN foreign_key_violation THEN
                -- Residual RESTRICT dependency still references this account —
                -- keep the tombstone, count + log, continue. Never aborts the
                -- purge. The counter surfaces the skip in v_result + the audit
                -- row so a future unhandled FK is loud, not silent (CLAUDE.md:
                -- "do not silently swallow errors").
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

    -- Step I (migration 117, extended by 129): clear ride_routes GPS geometry at
    -- 3y, mirroring Step A. Keeps per-phase distance/duration scalars; row
    -- cascades on the 7y rides delete.
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

    -- Step J (migration 141): delete AI assistant chat at 90d (Step D window).
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

    -- Step K (migration 143): delete surge_pricing history at 90d.
    IF NOT p_dry_run THEN
        DELETE FROM surge_pricing
        WHERE created_at < v_started_at - c_surge_history_age;
        GET DIAGNOSTICS v_surge_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_surge_deleted
        FROM surge_pricing
        WHERE created_at < v_started_at - c_surge_history_age;
    END IF;

    -- Step L (migration 228): price_searches — anonymize user_id at 90d
    -- (funnel only COUNT(*)s the table; an anonymized row is timestamp +
    -- area only), delete rows at 25 months (keeps the YTD prior-window
    -- funnel comparison exact, then bounds table growth).
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
        -- migration 256: which live column each drift-prone step actually used.
        -- Recorded so schema drift is visible in the daily audit row instead of
        -- only surfacing as a stack trace once it breaks.
        'retention_ts_columns',       jsonb_build_object(
            'ride_messages',  v_msgs_ts_col,
            'stripe_events',  v_stripe_ts_col
        )
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
    'B-P1-6 + B-P1-7 + B-P2-4 + DV-8 retention enforcement. Steps D and F (migration 256) resolve their retention timestamp column from pg_catalog at run time — ride_messages uses timestamp (migration 98 shape) or created_at (legacy 08/50 shape); stripe_events uses received_at (migration 22) or created_at — and RAISE if neither candidate exists. Both were hard-coded to a non-existent created_at by migration 67, which aborted every run with 42703 and rolled the whole purge back. Step C uses driver_location_history.received_at (migration 187). Step H (migration 216) HARD-DELETES DSAR-deleted accounts at 7y with NO anonymization once their regulatory footprint (rides / insurance periods / payouts / bank accounts) has cleared — Uber/Lyft attributable-retention model; drivers with insurance history stay tombstoned indefinitely. Step I (117/129) clears ride_routes geometry at 3y; Step J (141) ai_messages/ai_conversations at 90d; Step K (143) surge_pricing at 90d; Step L (228) anonymizes price_searches.user_id at 90d and deletes rows at 25 months. p_dry_run=true returns counts without mutating.';

NOTIFY pgrst, 'reload schema';
