-- ============================================================================
-- wipe_drivers_and_customers.sql
--
-- Purpose: hard-delete ALL drivers and/or ALL customers (riders) plus every
-- operational row that depends on them, so drivers can be re-loaded from CSV
-- (scripts/import_saskatoon_drivers.py) and their documents re-uploaded.
--
-- The importer de-dupes on users.phone / users.email and on
-- drivers.legacy_import_metadata (old_driver_id + source). Stale rows left
-- behind would make the re-import SKIP those drivers, so a clean wipe of the
-- old users + drivers + driver_documents rows is REQUIRED before reload.
--
-- ============================================================================
-- ⚠️  REGULATORY WARNING — READ BEFORE RUNNING IN PRODUCTION  ⚠️
-- ----------------------------------------------------------------------------
-- Spinr operates under the Saskatchewan Transportation Act + SGI insurance.
-- These tables carry MANDATORY retention that this script DELETES:
--     * rides                     — trip record, 7 years (tax/financial)
--     * financial_events          — 7 years
--     * driver_insurance_periods  — APPEND-ONLY, regulator-audited
--     * payouts / bank_accounts   — 7 years (T4A / financial)
-- A full driver/rider delete is IMPOSSIBLE without removing these: they hold
-- NO ACTION foreign keys to drivers/users/rides, so leaving them in place makes
-- the parent DELETE fail. That is why there is no "keep regulatory data" option
-- here — keeping them and deleting the account are mutually exclusive.
--
-- Two of them are also protected by append-only DB triggers that RAISE on any
-- DELETE (driver_insurance_periods_no_mutate on driver_insurance_periods;
-- financial_events_no_mutate on financial_events). Each part below DISABLEs
-- those two triggers for the duration of its transaction and re-ENABLEs them
-- before COMMIT — so the guard is only ever off inside the wipe, and any error
-- rolls back with the guard left intact. Disabling them requires running as the
-- table OWNER: the Supabase SQL editor / a direct connection as the `postgres`
-- role works; a restricted role will get "permission denied" on ALTER TABLE.
--
-- The COMPLIANT production path is NOT this script — it is migration 216's
-- purge_pii_retention() flow, which TOMBSTONES an account and only hard-deletes
-- it after the ~7-year footprint ages out. Use THIS script only against:
--     * a fresh / pre-launch environment with no real trip history, OR
--     * staging / a throwaway test schema, OR
--     * production ONLY with documented legal sign-off for a full purge.
--
-- What is PRESERVED by every part:
--     * admin accounts   (users.role = 'admin')  and admin_staff / staff
--     * configuration    service_areas, vehicle_types, fare_configs,
--                        document_requirements, settings, faqs, promotions
--   Part 1 (drivers) also PRESERVES rides.driver_id as-is: rides.driver_id has
--   no FK, so deleting the driver row keeps the trip↔driver linkage on the
--   (retained) ride record intact. Only Part 2 deletes rides.
--
-- HOW TO RUN
--   1. Take a snapshot / backup first (Supabase: point-in-time restore).
--   2. Run the file as-is first: only Part 0 (preview counts) executes; the
--      write parts are commented out. Review the counts.
--   3. Enable exactly the part you want and re-run. Each part is ONE
--      transaction — all-or-nothing.
--
--   WHICH PART?
--     * Part 3 (FULL RESET) — RECOMMENDED for "wipe all drivers AND customers,
--       reload from CSV". Removes every non-admin user, all rides, all drivers.
--       Handles users who are BOTH a driver and a rider (that mix is what makes
--       Part 1/Part 2 hit a rides_rider_id_fkey error when run alone).
--     * Part 1 (drivers only) — only when you want to KEEP riders and their
--       rides. Detaches any rides a driver booked as a passenger.
--     * Part 2 (customers only) — only when you want to KEEP drivers.
-- ============================================================================


-- ============================================================================
-- PART 0 — PREVIEW (read-only). Safe to run anytime; deletes nothing.
-- ============================================================================
-- (portable across psql and the Supabase SQL editor — no backslash commands)
SELECT 'users (role=driver)'      AS scope, count(*) AS n FROM users WHERE role = 'driver'
UNION ALL SELECT 'users (role=rider)',        count(*) FROM users WHERE role = 'rider'
UNION ALL SELECT 'users (role=admin) KEPT',   count(*) FROM users WHERE role = 'admin'
UNION ALL SELECT 'drivers (ALL)',             count(*) FROM drivers
UNION ALL SELECT 'driver_documents',          count(*) FROM driver_documents
UNION ALL SELECT 'rides (ALL)',               count(*) FROM rides;


-- ============================================================================
-- PART 1 — DELETE ALL DRIVERS  (drivers + their operational rows + driver users)
--          Run this before re-importing drivers from CSV.
--          Does NOT delete rides; leaves rides.driver_id untouched.
-- To enable: delete the line "/*  <<< enable Part 1" and its matching close.
-- ============================================================================
/*  <<< enable Part 1
BEGIN;

DO $$
DECLARE
    -- (table, column holding drivers.id) — cleared for EVERY driver.
    -- Includes the NO ACTION regulatory tables (payouts/bank_accounts/
    -- driver_insurance_periods): they MUST go or DELETE FROM drivers FK-fails.
    _driver_child text[][] := ARRAY[
        ['ride_offers',                    'driver_id'],
        ['payouts',                        'driver_id'],
        ['bank_accounts',                  'driver_id'],
        ['driver_insurance_periods',       'driver_id'],
        ['subscription_payments',          'driver_id'],
        ['driver_subscriptions',           'driver_id'],
        ['driver_documents',               'driver_id'],
        ['driver_daily_stats',             'driver_id'],
        ['driver_activity_log',            'driver_id'],
        ['driver_notes',                   'driver_id'],
        ['driver_bonuses',                 'driver_id'],
        ['driver_vehicle_history',         'driver_id'],
        ['driver_location_history',        'driver_id'],
        ['driver_onboarding_reminder_log', 'driver_id'],
        ['ride_incentive_claims',          'driver_id'],
        ['quest_progress',                 'driver_id']
    ];
    -- (table, column holding users.id) — cleared for the DRIVER user rows only.
    _user_child text[][] := ARRAY[
        ['saved_addresses',         'user_id'],
        ['emergency_contacts',      'user_id'],
        ['notifications',           'user_id'],
        ['notification_preferences','user_id'],
        ['push_retry_queue',        'user_id'],
        ['push_tokens',             'user_id'],
        ['support_tickets',         'user_id'],
        ['data_export_requests',    'user_id'],
        ['data_export_objects',     'user_id'],
        ['complaints',              'reporter_id'],
        ['lost_and_found',          'reporter_id'],
        ['disputes',                'user_id'],
        ['marketing_preferences',   'user_id'],
        ['marketing_consent_events','user_id'],
        ['marketing_suppressions',  'user_id'],
        ['loyalty_accounts',        'user_id'],
        ['loyalty_transactions',    'user_id'],
        ['wallets',                 'user_id'],
        ['wallet_transactions',     'user_id'],
        ['fare_splits',             'user_id'],
        ['fare_split_participants', 'user_id'],
        ['price_searches',          'user_id'],
        ['refresh_tokens',          'user_id'],
        ['financial_events',        'user_id']
    ];
    -- Append-only tamper-evidence triggers (SK Transportation Act) that raise on
    -- DELETE. They are disabled for THIS transaction only and re-enabled before
    -- COMMIT; any error rolls the whole thing back and leaves them enabled.
    -- ⚠️ Disabling these bypasses a regulatory guard — only run on a
    --    non-production / staging DB or with documented legal sign-off.
    _guard_triggers text[][] := ARRAY[
        ['driver_insurance_periods', 'driver_insurance_periods_no_mutate'],
        ['financial_events',         'financial_events_no_mutate']
    ];
    r text[];
    g text[];
    n integer;
BEGIN
    CREATE TEMP TABLE _victims ON COMMIT DROP AS
        SELECT id FROM users WHERE role = 'driver';

    -- disable append-only guards for this transaction
    FOREACH g SLICE 1 IN ARRAY _guard_triggers LOOP
        IF EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                   WHERE c.relname = g[1] AND t.tgname = g[2]) THEN
            EXECUTE format('ALTER TABLE %I DISABLE TRIGGER %I', g[1], g[2]);
            RAISE NOTICE 'disabled guard % on %', g[2], g[1];
        END IF;
    END LOOP;

    -- AI chat (ai_messages -> ai_conversations -> users)
    IF to_regclass('public.ai_conversations') IS NOT NULL THEN
        IF to_regclass('public.ai_messages') IS NOT NULL THEN
            EXECUTE 'DELETE FROM ai_messages WHERE conversation_id::text IN '
                 || '(SELECT id::text FROM ai_conversations WHERE user_id::text IN (SELECT id::text FROM _victims))';
        END IF;
        EXECUTE 'DELETE FROM ai_conversations WHERE user_id::text IN (SELECT id::text FROM _victims)';
    END IF;

    -- driver-keyed operational children (ALL drivers)
    FOREACH r SLICE 1 IN ARRAY _driver_child LOOP
        IF to_regclass('public.'||r[1]) IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name=r[1] AND column_name=r[2]) THEN
            EXECUTE format('DELETE FROM %I WHERE %I::text IN (SELECT id::text FROM drivers)', r[1], r[2]);
            GET DIAGNOSTICS n = ROW_COUNT;
            RAISE NOTICE 'cleared % : % rows', r[1], n;
        END IF;
    END LOOP;

    -- user-keyed children for the driver accounts
    FOREACH r SLICE 1 IN ARRAY _user_child LOOP
        IF to_regclass('public.'||r[1]) IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name=r[1] AND column_name=r[2]) THEN
            EXECUTE format('DELETE FROM %I WHERE %I::text IN (SELECT id::text FROM _victims)', r[1], r[2]);
            GET DIAGNOSTICS n = ROW_COUNT;
            RAISE NOTICE 'cleared % : % rows', r[1], n;
        END IF;
    END LOOP;

    -- null admin back-reference so the parent delete is not blocked
    IF to_regclass('public.reconciliation_discrepancies') IS NOT NULL THEN
        EXECUTE 'UPDATE reconciliation_discrepancies SET resolved_by = NULL '
             || 'WHERE resolved_by::text IN (SELECT id::text FROM _victims)';
    END IF;

    -- Some driver accounts ALSO booked rides as a passenger (rides.rider_id ->
    -- this driver user). rides.rider_id is nullable, so NULL it to detach the
    -- ride from the account being removed while KEEPING the trip record. Without
    -- this the users delete fails with rides_rider_id_fkey (23503).
    -- NOTE: if you want those rides GONE too, use PART 3 (full reset) instead.
    EXECUTE 'UPDATE rides SET rider_id = NULL WHERE rider_id::text IN (SELECT id::text FROM _victims)';
    GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'detached rides.rider_id from driver users : % rows', n;

    -- the drivers, then the driver user accounts.
    -- rides.driver_id has NO FK, so it stays as-is (trip linkage preserved).
    EXECUTE 'DELETE FROM drivers';
    GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'deleted drivers : % rows', n;

    EXECUTE 'DELETE FROM users WHERE id::text IN (SELECT id::text FROM _victims)';
    GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'deleted driver users : % rows', n;

    -- re-enable the append-only guards before COMMIT
    FOREACH g SLICE 1 IN ARRAY _guard_triggers LOOP
        IF EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                   WHERE c.relname = g[1] AND t.tgname = g[2]) THEN
            EXECUTE format('ALTER TABLE %I ENABLE TRIGGER %I', g[1], g[2]);
            RAISE NOTICE 're-enabled guard % on %', g[2], g[1];
        END IF;
    END LOOP;
END $$;

-- Sanity: expect 0 / 0
SELECT count(*) AS drivers_left FROM drivers;
SELECT count(*) AS driver_users_left FROM users WHERE role = 'driver';

COMMIT;
*/  -- <<< enable Part 1 (delete this line too)


-- ============================================================================
-- PART 2 — DELETE ALL CUSTOMERS (riders) + their rides + ride/rider children.
--          Independent of Part 1. Preserves admins and configuration.
-- To enable: delete the line "/*  <<< enable Part 2" and its matching close.
-- ============================================================================
/*  <<< enable Part 2
BEGIN;

DO $$
DECLARE
    -- (table, column holding rides.id) — cleared for EVERY ride.
    _ride_child text[][] := ARRAY[
        ['ride_routes',           'ride_id'],
        ['ride_offers',           'ride_id'],
        ['ride_messages',         'ride_id'],
        ['ride_live_activities',  'ride_id'],
        ['ride_payment_sources',  'ride_id'],
        ['ride_incentive_claims', 'ride_id'],
        ['stripe_disputes',       'ride_id'],
        ['disputes',              'ride_id']
    ];
    -- (table, column holding users.id) — cleared for the RIDER user rows.
    _user_child text[][] := ARRAY[
        ['saved_addresses',         'user_id'],
        ['emergency_contacts',      'user_id'],
        ['notifications',           'user_id'],
        ['notification_preferences','user_id'],
        ['push_retry_queue',        'user_id'],
        ['push_tokens',             'user_id'],
        ['support_tickets',         'user_id'],
        ['data_export_requests',    'user_id'],
        ['data_export_objects',     'user_id'],
        ['complaints',              'reporter_id'],
        ['lost_and_found',          'reporter_id'],
        ['disputes',                'user_id'],
        ['marketing_preferences',   'user_id'],
        ['marketing_consent_events','user_id'],
        ['marketing_suppressions',  'user_id'],
        ['loyalty_accounts',        'user_id'],
        ['loyalty_transactions',    'user_id'],
        ['wallets',                 'user_id'],
        ['wallet_transactions',     'user_id'],
        ['fare_splits',             'user_id'],
        ['fare_split_participants', 'user_id'],
        ['price_searches',          'user_id'],
        ['refresh_tokens',          'user_id']
    ];
    -- Append-only tamper-evidence triggers (SK Transportation Act) that raise on
    -- DELETE. Disabled for THIS transaction only and re-enabled before COMMIT.
    -- ⚠️ Bypasses a regulatory guard — staging / legally-signed-off only.
    _guard_triggers text[][] := ARRAY[
        ['driver_insurance_periods', 'driver_insurance_periods_no_mutate'],
        ['financial_events',         'financial_events_no_mutate']
    ];
    r text[];
    g text[];
    n integer;
BEGIN
    CREATE TEMP TABLE _victims ON COMMIT DROP AS
        SELECT id FROM users WHERE role = 'rider';

    -- disable append-only guards for this transaction
    FOREACH g SLICE 1 IN ARRAY _guard_triggers LOOP
        IF EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                   WHERE c.relname = g[1] AND t.tgname = g[2]) THEN
            EXECUTE format('ALTER TABLE %I DISABLE TRIGGER %I', g[1], g[2]);
            RAISE NOTICE 'disabled guard % on %', g[2], g[1];
        END IF;
    END LOOP;

    -- AI chat
    IF to_regclass('public.ai_conversations') IS NOT NULL THEN
        IF to_regclass('public.ai_messages') IS NOT NULL THEN
            EXECUTE 'DELETE FROM ai_messages WHERE conversation_id::text IN '
                 || '(SELECT id::text FROM ai_conversations WHERE user_id::text IN (SELECT id::text FROM _victims))';
        END IF;
        EXECUTE 'DELETE FROM ai_conversations WHERE user_id::text IN (SELECT id::text FROM _victims)';
    END IF;

    -- financial_events references BOTH users and rides (NO ACTION on each) —
    -- clear every row tied to a rider OR to any ride before deleting rides/users.
    IF to_regclass('public.financial_events') IS NOT NULL THEN
        EXECUTE 'DELETE FROM financial_events WHERE user_id::text IN (SELECT id::text FROM _victims) '
             || 'OR ride_id::text IN (SELECT id::text FROM rides)';
    END IF;

    -- driver_insurance_periods references rides (NO ACTION) — clear its ride ref.
    IF to_regclass('public.driver_insurance_periods') IS NOT NULL THEN
        EXECUTE 'DELETE FROM driver_insurance_periods WHERE ride_id::text IN (SELECT id::text FROM rides)';
    END IF;

    -- ride-keyed children (ALL rides)
    FOREACH r SLICE 1 IN ARRAY _ride_child LOOP
        IF to_regclass('public.'||r[1]) IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name=r[1] AND column_name=r[2]) THEN
            EXECUTE format('DELETE FROM %I WHERE %I::text IN (SELECT id::text FROM rides)', r[1], r[2]);
            GET DIAGNOSTICS n = ROW_COUNT;
            RAISE NOTICE 'cleared % : % rows', r[1], n;
        END IF;
    END LOOP;

    -- user-keyed children for the rider accounts
    FOREACH r SLICE 1 IN ARRAY _user_child LOOP
        IF to_regclass('public.'||r[1]) IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name=r[1] AND column_name=r[2]) THEN
            EXECUTE format('DELETE FROM %I WHERE %I::text IN (SELECT id::text FROM _victims)', r[1], r[2]);
            GET DIAGNOSTICS n = ROW_COUNT;
            RAISE NOTICE 'cleared % : % rows', r[1], n;
        END IF;
    END LOOP;

    -- the rides, then the rider user accounts
    EXECUTE 'DELETE FROM rides WHERE rider_id::text IN (SELECT id::text FROM _victims)';
    GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'deleted rides : % rows', n;

    EXECUTE 'DELETE FROM users WHERE id::text IN (SELECT id::text FROM _victims)';
    GET DIAGNOSTICS n = ROW_COUNT; RAISE NOTICE 'deleted rider users : % rows', n;

    -- re-enable the append-only guards before COMMIT
    FOREACH g SLICE 1 IN ARRAY _guard_triggers LOOP
        IF EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                   WHERE c.relname = g[1] AND t.tgname = g[2]) THEN
            EXECUTE format('ALTER TABLE %I ENABLE TRIGGER %I', g[1], g[2]);
            RAISE NOTICE 're-enabled guard % on %', g[2], g[1];
        END IF;
    END LOOP;
END $$;

SELECT count(*) AS rider_users_left FROM users WHERE role = 'rider';

COMMIT;
*/  -- <<< enable Part 2 (delete this line too)


-- ============================================================================
-- PART 3 — FULL RESET  (RECOMMENDED for "delete all drivers AND customers,
--          then reload from CSV").
--
-- Deletes EVERY non-admin account (riders AND drivers), ALL rides, ALL drivers,
-- and every operational/dependent row — in one transaction. Because everything
-- non-admin goes at once, cross-references like a driver who also booked rides
-- as a passenger (the rides_rider_id_fkey error) cannot block it.
--
-- PRESERVES only: admin accounts (role='admin'), admin_staff/staff, and the
-- configuration tables (service_areas, vehicle_types, fare_configs,
-- document_requirements, settings, faqs, promotions).
--
-- To enable: delete the line "/*  <<< enable Part 3" and its matching close.
-- ============================================================================
/*  <<< enable Part 3
BEGIN;

DO $$
DECLARE
    -- child tables keyed on rides.id  (cleared wholesale — every ride is going)
    _ride_child text[][] := ARRAY[
        ['ride_routes','ride_id'], ['ride_offers','ride_id'], ['ride_messages','ride_id'],
        ['ride_live_activities','ride_id'], ['ride_payment_sources','ride_id'],
        ['ride_incentive_claims','ride_id'], ['stripe_disputes','ride_id'], ['disputes','ride_id']
    ];
    -- child tables keyed on drivers.id (cleared wholesale — every driver is going)
    _driver_child text[][] := ARRAY[
        ['ride_offers','driver_id'], ['payouts','driver_id'], ['bank_accounts','driver_id'],
        ['subscription_payments','driver_id'], ['driver_subscriptions','driver_id'],
        ['driver_documents','driver_id'], ['driver_daily_stats','driver_id'],
        ['driver_activity_log','driver_id'], ['driver_notes','driver_id'],
        ['driver_bonuses','driver_id'], ['driver_vehicle_history','driver_id'],
        ['driver_location_history','driver_id'], ['driver_onboarding_reminder_log','driver_id'],
        ['ride_incentive_claims','driver_id'], ['quest_progress','driver_id']
    ];
    -- child tables keyed on users.id (scoped to non-admin so admin data survives)
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

-- Sanity: expect 0 / 0 / 0 and admins preserved
SELECT count(*) AS rides_left FROM rides;
SELECT count(*) AS drivers_left FROM drivers;
SELECT count(*) AS non_admin_users_left FROM users WHERE role <> 'admin';
SELECT count(*) AS admins_kept FROM users WHERE role = 'admin';

COMMIT;
*/  -- <<< enable Part 3 (delete this line too)
