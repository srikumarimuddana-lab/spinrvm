-- ─────────────────────────────────────────────────────────────────────────────
-- 436_purge_retention_never_revoked_refresh_tokens.sql
--
-- WHAT THIS FIXES
--   purge_pii_retention()'s Step E never deleted a refresh_tokens row whose
--   revoked_at stayed NULL. Only explicitly-revoked rows (rotation, logout,
--   reuse-cascade) were purged. A token that was issued, used once or never,
--   and then simply expired was retained FOREVER -- together with the
--   user_agent and ip columns on that row.
--
--   That was latent until 2026-09-21: rider/driver sessions had been storing a
--   constant Fly edge-proxy address (172.16.x.x) in `ip`, which is not personal
--   information. PR #5654 fixed that resolution bug so `ip` now holds the real
--   client IP -- which turned an unbounded retention of a useless constant into
--   an unbounded retention of identifying PII. Hence this migration.
--
-- HOW IT REGRESSED
--   50_pii_retention_purge.sql:212-223 purged on `expires_at < cutoff`.
--   Between 50 and 117 the predicate narrowed to `revoked_at IS NOT NULL AND
--   revoked_at < cutoff` and was carried verbatim through every subsequent
--   CREATE OR REPLACE, most recently 434. Two docs still describe the ORIGINAL
--   behaviour and were wrong until this migration restored it:
--     backend/utils/retention_purge.py:19
--     docs/runbooks/data-retention.md:34
--
-- WHAT CHANGES
--   Step E only. The predicate gains a second arm for never-revoked rows:
--       OR (revoked_at IS NULL AND expires_at < cutoff)
--   Every other step is copied byte-for-byte from 434. Diff 434 -> 436 to
--   confirm: Step E's body and its comment are the only executable change.
--
-- ⚠ RUN THE DRY RUN FIRST. This widens a DELETE to rows it has never touched,
--   on a table that has been accumulating unpurged rows for the life of the
--   bug. Measure before applying:
--
--     -- 1. Exactly how many rows the new arm adds:
--     SELECT
--       count(*) FILTER (WHERE revoked_at IS NOT NULL
--                          AND revoked_at < now() - interval '30 days')
--         AS purgeable_before_436,
--       count(*) FILTER (WHERE revoked_at IS NULL
--                          AND expires_at < now() - interval '30 days')
--         AS newly_purgeable_by_436,
--       count(*) AS total_rows
--     FROM refresh_tokens;
--
--     -- 2. Whole-function dry run (mutates nothing; returns per-step counts):
--     SELECT purge_pii_retention(true);
--
--   `newly_purgeable_by_436` is this migration's entire blast radius. If it is
--   large enough that a single DELETE would hold locks too long, run the purge
--   in batches out-of-band BEFORE applying, rather than widening the predicate
--   and letting the scheduled job take it all at once.
--
-- SAFETY
--   Only rows already invalid for 30+ days are touched. A row matching the new
--   arm has expires_at in the past, so lookup_refresh_token (:313-314) already
--   returns NULL for it -- no live session can be signed out by this. Deleting
--   it cannot revoke access that still exists.
--
-- ROLLBACK PLAN (migrations/CLAUDE.md: always reversible on paper)
--   Re-apply 434's definition of purge_pii_retention() verbatim; it is a pure
--   CREATE OR REPLACE with no DDL, so reverting the function is instant and
--   needs no second deploy. NOTE: rows already deleted by a run of this version
--   are NOT recoverable by that revert -- restore them from a PITR snapshot if
--   they are needed. This is precisely why the dry run above is mandatory
--   rather than advisory. No schema change, no data backfill, no index change.
--
-- migration-override-ok: redefines purge_pii_retention() (RPC-by-name caller,
-- see migration 289's header for why it can't be renamed) -- same intentional
-- re-fork pattern as migrations 296/321/323/324/335/434.
--
-- NOTE on the OTHER gate: this file also trips the dangerous-ops check on
-- `DELETE FROM`. That check has NO inline override, by deliberate design (see
-- migration-check.yml's comment above its `code_only_strict` block): an
-- intentional exception goes through a GitHub Change Request plus an admin
-- merge-override, never a code annotation. This file's DELETE FROM statements
-- live inside the purge_pii_retention() function BODY -- nothing executes at
-- migration-apply time, only when the retention job next calls the function --
-- which is exactly the CR-2026-033 precedent that check's own comment cites.
-- Do NOT restructure the SQL to evade that regex; dodging it is precisely what
-- the gate exists to prevent. See the CR linked from this migration's PR.
--
-- WHAT THIS DOES NOT DO
--   Does not backfill or correct historical `ip` values -- rows written before
--   PR #5654 hold the proxy address and the real IP was never captured.
--   Does not change the 30-day grace constant.
--   Does not touch any other step, table, or index.
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
    -- Step A (335): anonymize ride GPS at 3y. `planned_route_polyline`
    -- (migration 100) added here -- see migration 335's own header.
    IF NOT p_dry_run THEN
        UPDATE rides
        SET pickup_lat             = NULL,
            pickup_lng             = NULL,
            dropoff_lat            = NULL,
            dropoff_lng            = NULL,
            route_polyline         = '[]'::jsonb,
            phase_polylines        = '{}'::jsonb,
            route_snapshot_url     = NULL,
            planned_route_polyline = '[]'::jsonb,
            gps_anonymized_at      = v_started_at
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
    -- `timestamp`, not `created_at` -- see migration 323's own header.
    IF NOT p_dry_run THEN
        DELETE FROM ride_messages
        WHERE "timestamp" < v_started_at - c_chat_age;
        GET DIAGNOSTICS v_msgs_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_msgs_deleted
        FROM ride_messages
        WHERE "timestamp" < v_started_at - c_chat_age;
    END IF;

    -- Step E (436): purge refresh_tokens 30 days after they became invalid --
    -- by EITHER route, explicit revocation or natural expiry.
    --
    -- Migration 50 purged on expires_at. Between 50 and 117 the filter
    -- narrowed to `revoked_at IS NOT NULL AND revoked_at < cutoff` and stayed
    -- that way through every CREATE OR REPLACE since. revoked_at is only ever
    -- stamped by an explicit act -- rotation (utils/refresh_tokens.py:191),
    -- revoke (:636), logout-all (:680). Natural expiry stamps nothing:
    -- lookup_refresh_token (:313-314) returns NULL on an expired row and
    -- writes no column. So a token issued and never used again -- a rider who
    -- takes one ride and never reopens the app, an abandoned install -- kept
    -- revoked_at = NULL forever and was NEVER purged, retaining user_agent
    -- and ip with no ceiling.
    --
    -- The second arm restores migration 50's intent. The first arm is KEPT,
    -- not replaced: revocation can fire well before expiry (a token revoked
    -- on day 1 of a 30-day lifetime is purgeable on day 31, not day 61), so
    -- dropping it would lengthen retention for the rows that are currently
    -- handled correctly.
    --
    -- expires_at is NOT NULL (08_complete_schema.sql:168), so the two arms
    -- together cover every row -- no third NULL-expiry case to leak through.
    -- The second arm is served exactly by the existing partial index
    -- idx_refresh_tokens_expires ON refresh_tokens (expires_at)
    -- WHERE revoked_at IS NULL (25_refresh_tokens_and_token_version.sql:62-64),
    -- so this adds no new index and no seq scan.
    IF NOT p_dry_run THEN
        DELETE FROM refresh_tokens
        WHERE (revoked_at IS NOT NULL AND revoked_at < v_started_at - c_token_grace_age)
           OR (revoked_at IS NULL     AND expires_at < v_started_at - c_token_grace_age);
        GET DIAGNOSTICS v_tokens_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_tokens_deleted
        FROM refresh_tokens
        WHERE (revoked_at IS NOT NULL AND revoked_at < v_started_at - c_token_grace_age)
           OR (revoked_at IS NULL     AND expires_at < v_started_at - c_token_grace_age);
    END IF;

    -- Step F (324): delete stripe_events at 90d.
    -- `received_at`, not `created_at` -- see migration 324's own header.
    IF NOT p_dry_run THEN
        DELETE FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
        GET DIAGNOSTICS v_stripe_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_stripe_deleted
        FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
    END IF;

    -- Step G (434, C112 fix): delete audit_logs at 7y (gated by
    -- session-flag). Restores the PERFORM set_config(...) call migration
    -- 335's Step G was missing, and wraps the DELETE in the same
    -- BEGIN/EXCEPTION WHEN OTHERS/RAISE shape Step H/M use below -- required
    -- together with this migration's trigger fix (narrowing migration 57's
    -- audit_logs_no_mutate to UPDATE-only) so a real error here can no
    -- longer roll back every other retention step in this function. See
    -- ACTION_ITEMS.md C112.
    IF NOT p_dry_run THEN
        PERFORM set_config('spinr.audit_logs.allow_delete', 'true', true);
        BEGIN
            DELETE FROM audit_logs
            WHERE created_at < v_started_at - c_audit_log_age;
            GET DIAGNOSTICS v_audit_deleted = ROW_COUNT;
        EXCEPTION WHEN OTHERS THEN
            PERFORM set_config('spinr.audit_logs.allow_delete', 'false', true);
            RAISE;
        END;
        PERFORM set_config('spinr.audit_logs.allow_delete', 'false', true);
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
    'B-P1-6 + B-P1-7 + B-P2-4 + DV-8 + B17 + B18 + A38 + Step-D-fix (323) + Step-F-fix (324) + Step-A-planned-route-polyline-fix (335) + Step-G-delete-gate-fix (434, C112) + Step-E-never-revoked-fix (436) retention enforcement. Step A also clears planned_route_polyline (100) alongside route_polyline/phase_polylines (335). Step B (7y rides) relies on financial_events_ride_id_fkey ON DELETE SET NULL (294/295). Step C uses driver_location_history.received_at (187). Step D uses ride_messages.timestamp, not created_at (323). Step F uses stripe_events.received_at, not created_at (324). Step G (434) sets spinr.audit_logs.allow_delete around its DELETE, wrapped in BEGIN/EXCEPTION so a failure there cannot roll back every other step -- see migration 434''s header for why this matters together with the trigger fix it ships with. Step H (216, driver-ride guard added by A38/321) HARD-DELETES DSAR-deleted accounts at 7y with NO anonymization once their regulatory footprint (rides as rider OR driver, driver_insurance_periods, payouts, bank_accounts) has cleared. Step N (296) scrubs profile PII (name/email/profile_image/saved_addresses) 30 days after a DSAR deletion request, independent of Step H''s 7y window, per regulatory-sk.md Right-to-delete #1. Step E (436) purges refresh_tokens 30d after revocation OR after natural expiry; before 436 it only purged explicitly-revoked rows, so never-revoked tokens (and their user_agent/ip) were retained indefinitely. p_dry_run=true returns counts without mutating.';

NOTIFY pgrst, 'reload schema';
