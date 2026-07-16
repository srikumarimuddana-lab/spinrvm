-- ============================================================================
-- full_reset_now.sql  —  READY TO RUN (nothing commented out).
--
-- Deletes EVERY non-admin account (all riders AND all drivers), ALL rides, ALL
-- drivers, and every dependent operational row, in ONE transaction. Then you can
-- reload drivers from CSV (scripts/import_saskatoon_drivers.py) and re-upload
-- documents.
--
-- PRESERVES: admin accounts (users.role='admin'), admin_staff/staff, and config
-- tables (service_areas, vehicle_types, fare_configs, document_requirements,
-- settings, faqs, promotions).
--
-- ⚠️ Deletes regulator-retained tables (rides, financial_events,
--    driver_insurance_periods, payouts, bank_accounts) and briefly disables
--    their append-only guard triggers. Run ONLY on a staging / pre-launch DB or
--    with documented legal sign-off. Take a backup/snapshot first.
--
-- ⚠️ Must run as the table OWNER (Supabase SQL editor / `postgres` role) — the
--    trigger DISABLE needs ownership. Run the WHOLE script at once (it is one
--    transaction; do not run line-by-line).
-- ============================================================================

BEGIN;

DO $$
DECLARE
    _ride_child text[][] := ARRAY[
        ['ride_routes','ride_id'], ['ride_offers','ride_id'], ['ride_messages','ride_id'],
        ['ride_live_activities','ride_id'], ['ride_payment_sources','ride_id'],
        ['ride_incentive_claims','ride_id'], ['stripe_disputes','ride_id'], ['disputes','ride_id']
    ];
    _driver_child text[][] := ARRAY[
        ['ride_offers','driver_id'], ['payouts','driver_id'], ['bank_accounts','driver_id'],
        ['subscription_payments','driver_id'], ['driver_subscriptions','driver_id'],
        ['driver_documents','driver_id'], ['driver_daily_stats','driver_id'],
        ['driver_activity_log','driver_id'], ['driver_notes','driver_id'],
        ['driver_bonuses','driver_id'], ['driver_vehicle_history','driver_id'],
        ['driver_location_history','driver_id'], ['driver_onboarding_reminder_log','driver_id'],
        ['ride_incentive_claims','driver_id'], ['quest_progress','driver_id']
    ];
    _user_child text[][] := ARRAY[
        ['saved_addresses','user_id'], ['emergency_contacts','user_id'],
        ['notifications','user_id'], ['notification_preferences','user_id'],
        ['push_retry_queue','user_id'], ['push_tokens','user_id'],
        ['support_tickets','user_id'], ['data_export_requests','user_id'],
        ['data_export_objects','user_id'], ['complaints','reporter_id'],
        ['lost_and_found','reporter_id'], ['disputes','user_id'],
        ['marketing_preferences','user_id'], ['marketing_consent_events','user_id'],
        ['marketing_suppressions','user_id'], ['loyalty_accounts','user_id'],
        ['loyalty_transactions','user_id'], ['wallets','user_id'],
        ['wallet_transactions','user_id'], ['fare_splits','user_id'],
        ['fare_split_participants','user_id'], ['price_searches','user_id'],
        ['refresh_tokens','user_id']
    ];
    _guard_triggers text[][] := ARRAY[
        ['driver_insurance_periods','driver_insurance_periods_no_mutate'],
        ['financial_events','financial_events_no_mutate']
    ];
    r text[];
    g text[];
    n integer;
BEGIN
    CREATE TEMP TABLE _victims ON COMMIT DROP AS
        SELECT id FROM users WHERE role <> 'admin';
    SELECT count(*) INTO n FROM _victims;
    RAISE NOTICE 'non-admin users to delete: %', n;

    -- disable append-only guards for this transaction
    FOREACH g SLICE 1 IN ARRAY _guard_triggers LOOP
        IF EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                   WHERE c.relname = g[1] AND t.tgname = g[2]) THEN
            EXECUTE format('ALTER TABLE %I DISABLE TRIGGER %I', g[1], g[2]);
            RAISE NOTICE 'disabled guard % on %', g[2], g[1];
        END IF;
    END LOOP;

    -- AI chat for the non-admin accounts
    IF to_regclass('public.ai_conversations') IS NOT NULL THEN
        IF to_regclass('public.ai_messages') IS NOT NULL THEN
            EXECUTE 'DELETE FROM ai_messages WHERE conversation_id::text IN '
                 || '(SELECT id::text FROM ai_conversations WHERE user_id::text IN (SELECT id::text FROM _victims))';
        END IF;
        EXECUTE 'DELETE FROM ai_conversations WHERE user_id::text IN (SELECT id::text FROM _victims)';
    END IF;

    -- append-only regulatory ledgers (guards disabled) — wholesale
    IF to_regclass('public.financial_events') IS NOT NULL THEN
        EXECUTE 'DELETE FROM financial_events'; END IF;
    IF to_regclass('public.driver_insurance_periods') IS NOT NULL THEN
        EXECUTE 'DELETE FROM driver_insurance_periods'; END IF;

    -- ride-keyed children (all rides going)
    FOREACH r SLICE 1 IN ARRAY _ride_child LOOP
        IF to_regclass('public.'||r[1]) IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name=r[1] AND column_name=r[2]) THEN
            EXECUTE format('DELETE FROM %I', r[1]);
            GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'cleared % : % rows', r[1], n;
        END IF;
    END LOOP;

    -- driver-keyed children (all drivers going)
    FOREACH r SLICE 1 IN ARRAY _driver_child LOOP
        IF to_regclass('public.'||r[1]) IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name=r[1] AND column_name=r[2]) THEN
            EXECUTE format('DELETE FROM %I', r[1]);
            GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'cleared % : % rows', r[1], n;
        END IF;
    END LOOP;

    -- user-keyed children (scoped to non-admin so admin rows survive)
    FOREACH r SLICE 1 IN ARRAY _user_child LOOP
        IF to_regclass('public.'||r[1]) IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name=r[1] AND column_name=r[2]) THEN
            EXECUTE format('DELETE FROM %I WHERE %I::text IN (SELECT id::text FROM _victims)', r[1], r[2]);
            GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'cleared % : % rows', r[1], n;
        END IF;
    END LOOP;

    -- null admin back-references so the users delete is not blocked
    IF to_regclass('public.reconciliation_discrepancies') IS NOT NULL THEN
        EXECUTE 'UPDATE reconciliation_discrepancies SET resolved_by = NULL '
             || 'WHERE resolved_by::text IN (SELECT id::text FROM _victims)';
    END IF;

    -- parents, deepest first: rides -> drivers -> non-admin users
    EXECUTE 'DELETE FROM rides';
    GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'deleted rides : % rows', n;
    EXECUTE 'DELETE FROM drivers';
    GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'deleted drivers : % rows', n;
    EXECUTE 'DELETE FROM users WHERE id::text IN (SELECT id::text FROM _victims)';
    GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'deleted non-admin users : % rows', n;

    -- re-enable the append-only guards before COMMIT
    FOREACH g SLICE 1 IN ARRAY _guard_triggers LOOP
        IF EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                   WHERE c.relname = g[1] AND t.tgname = g[2]) THEN
            EXECUTE format('ALTER TABLE %I ENABLE TRIGGER %I', g[1], g[2]);
            RAISE NOTICE 're-enabled guard % on %', g[2], g[1];
        END IF;
    END LOOP;
END $$;

-- Verify: expect 0 / 0 / 0, admins preserved.
SELECT
    (SELECT count(*) FROM rides)                          AS rides_left,
    (SELECT count(*) FROM drivers)                        AS drivers_left,
    (SELECT count(*) FROM users WHERE role <> 'admin')    AS non_admin_users_left,
    (SELECT count(*) FROM users WHERE role =  'admin')    AS admins_kept;

COMMIT;
