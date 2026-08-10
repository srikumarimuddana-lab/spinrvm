-- 296_pipeda_30day_profile_scrub.sql
-- B18 (partial): implement the 30-day profile-PII scrub that
-- .claude/context/regulatory-sk.md's Right-to-delete section promises but
-- that nothing in the codebase currently does.
--
-- Context / decision recorded here (ACTION_ITEMS.md B18 was filed
-- specifically because reconciling policy-vs-code unilaterally would be
-- deciding the underlying question rather than surfacing it -- this
-- migration is the product-owner-directed decision on the narrow part of
-- that question that doesn't require re-litigating migration 216):
--
--   B18 found THREE governing docs promising "anonymized after the
--   retention window, not deleted" while migration 216 (made operative by
--   289) actually hard-deletes DSAR accounts with no anonymization at 7
--   years (Step H). This migration does NOT reverse that decision -- Step H
--   stays exactly as it is. Un-shipping an already-operative hard-delete
--   model in favour of anonymization is a materially larger, riskier change
--   (touches Step H's per-account subtransaction logic, the
--   driver_insurance_periods/payouts/bank_accounts eligibility guard, and
--   the "sign in again anytime to reactivate" promise in
--   delete_account_pipeda's own response) than this migration's scope, and
--   per B18's own filing, un-doing it needs real legal/founder review, not
--   an agent session's unilateral call.
--
--   What THIS migration closes is narrower and unambiguous: regulatory-sk.md
--   line 86 promises "Personal profile fields (name, email, home address,
--   payment methods) -> scrubbed within 30 days" of a deletion request.
--   Nothing implements this today. Reading routes/users.py::
--   delete_account_pipeda (the actual DSAR endpoint real users hit) shows it
--   only sets deletion_requested_at/deletion_scheduled_at/status and revokes
--   tokens -- profile fields (name, email, profile_image) and
--   saved_addresses stay fully live and queryable for the entire 7-year
--   window until Step H's hard delete. That is a straightforwardly broken
--   promise, independent of the anonymize-vs-delete question, and closing
--   it is strictly additive to user privacy (it reduces what stays exposed
--   during the 7-year window, it does not reduce what Step H eventually does).
--
-- What is explicitly NOT done here, and why:
--   regulatory-sk.md line 43 also promises "Rider identity linked to trip:
--   7 years (hashed after 2)" -- a *general* retention rule (applies to
--   every ride, not just DSAR-requested ones). Implementing this literally
--   (hashing/nulling rides.rider_id at 2 years) would break every active
--   rider's own "my trips" screen for any ride older than 2 years, and any
--   admin/support/refund lookup by rider for that ride -- a live, real-money,
--   real-user-facing regression, not a narrow backend fix. That needs its
--   own design call (exclude vs. a separate access-controlled identity
--   store vs. something else) with real product/legal sign-off given the
--   UX cost, so it is deliberately left as an open gap here rather than
--   shipped under this migration. Tracked in ACTION_ITEMS.md's B18 update.
--
-- Mechanics:
--   1. New column users.profile_scrubbed_at (append-only marker, mirrors
--      rides.gps_anonymized_at -- never reset to NULL once set).
--   2. New Step N in purge_pii_retention(): for accounts with
--      status='pending_deletion' and deletion_requested_at more than 30
--      days ago and profile_scrubbed_at still NULL, NULL out first_name,
--      last_name, email, profile_image and hard-delete saved_addresses.
--      Anchored on deletion_requested_at (the actual DSAR request
--      timestamp), NOT deletion_scheduled_at (which is request + 7y, the
--      Step H hard-delete eligibility field) -- a different field for a
--      different purpose, per routes/users.py's own distinction.
--   3. Deliberately does NOT touch phone: delete_account_pipeda's own
--      response promises "sign in again anytime to reactivate," which is a
--      phone-OTP login. Nulling phone would break that promise. This
--      matches regulatory-sk.md line 86 itself, which lists name/email/
--      home-address/payment-methods -- not phone.
--   4. Payment methods: Spinr holds no local payment_methods table --
--      Stripe is the system of record (CLAUDE.md's PIPEDA section: "Payment
--      card numbers -- Stripe handles; never log even masked PANs"). A
--      Stripe-side detach/delete on the customer object is a Stripe API
--      call, not a DB purge step, and is out of scope for this SQL
--      migration -- flagged as a follow-up rather than silently assumed
--      handled.
--
-- Blast radius: users.first_name/last_name/email/profile_image and
-- saved_addresses, for pending_deletion accounts only (status is set
-- exactly once, by delete_account_pipeda, and reversed only by
-- reactivation login before Step N or Step H run -- see the recheck below).
-- Grepped consumers of users.email/first_name/last_name outside the DSAR
-- flow: rider/driver profile reads (blocked anyway -- token_version was
-- already bumped and every token revoked at request time, so a
-- pending_deletion account cannot make an authenticated request that would
-- read its own now-scrubbed fields), admin dashboard user search/detail
-- (expected to show a scrubbed row for a pending-deletion account -- this
-- is the intended behavior), and receipt/notification generation (rides
-- already carry their own fare/tax snapshot independent of the live users
-- row -- see financial_events.metadata). No dispatch, fare, or payment code
-- path reads these fields for a pending_deletion account, since such an
-- account cannot hold an active ride (_assert_deletable in routes/users.py
-- refuses deletion while a ride/payout/balance is outstanding) and cannot
-- log back in as a driver to go online (driver row status is separately
-- tombstoned by _tombstone_driver_row at request time).
--
-- Reactivation race: a user can reactivate (OTP login) any time before
-- Step N runs, which should un-set status/deletion_requested_at through the
-- existing reactivation path -- Step N's WHERE clause re-reads
-- status='pending_deletion' at execution time (not a snapshot), so a
-- reactivated account is simply excluded, exactly like Step H's own TOCTOU
-- recheck (migration 216/289) for the same reason.
--
-- Rollback: DROP the Step N block via CREATE OR REPLACE back to migration
-- 289's purge_pii_retention() body; the profile_scrubbed_at column can stay
-- (additive, harmless if unused) or be dropped with
-- `ALTER TABLE users DROP COLUMN profile_scrubbed_at` if a full revert is
-- needed. No backfill required either way.
--
-- Forward-compatible: new column (nullable, no default-computation on
-- existing rows) + CREATE OR REPLACE of an existing function. Safe against
-- live traffic.
--
-- migration-override-ok: redefines purge_pii_retention() (RPC-by-name
-- caller, see migration 289's header for why it can't be renamed).

ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_scrubbed_at timestamptz;

COMMENT ON COLUMN users.profile_scrubbed_at IS
    'Set once by purge_pii_retention() Step N (migration 296) when a '
    'pending_deletion account''s profile PII (name, email, profile_image, '
    'saved_addresses) is scrubbed 30 days after deletion_requested_at, per '
    'regulatory-sk.md''s Right-to-delete #1. Append-only -- never reset to '
    'NULL once set (mirrors rides.gps_anonymized_at).';

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
    'B-P1-6 + B-P1-7 + B-P2-4 + DV-8 + B17 + B18 retention enforcement. Step B (7y rides) relies on financial_events_ride_id_fkey ON DELETE SET NULL (294/295). Step H (216) HARD-DELETES DSAR-deleted accounts at 7y with NO anonymization once their regulatory footprint has cleared. Step N (296) scrubs profile PII (name/email/profile_image/saved_addresses) 30 days after a DSAR deletion request, independent of Step H''s 7y window, per regulatory-sk.md Right-to-delete #1. p_dry_run=true returns counts without mutating.';

NOTIFY pgrst, 'reload schema';
