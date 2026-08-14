-- =============================================================================
-- DELETE TEST DRIVER ACCOUNTS — full cleanup by phone number
-- =============================================================================
-- PURPOSE: Remove all data for test driver accounts from Supabase.
--
-- USAGE:
--   1. Replace the phone numbers in the array below with the actual test
--      account phone numbers.
--   2. Run with DRY RUN first (p_dry_run = true) to see what would be deleted.
--   3. Once satisfied, set p_dry_run = false to execute the actual deletion.
--
-- WARNING: This script bypasses append-only triggers. Only use for test
-- accounts. NEVER run against production user data without legal review.
--
-- NOTE: This does NOT delete Stripe Connect accounts on Stripe's side.
-- You must separately deactivate/delete those via the Stripe Dashboard
-- or API using the stripe_account_id values printed in the dry-run output.
-- =============================================================================

DO $$
DECLARE
    -- =========================================================================
    -- CONFIGURATION — edit these two values
    -- =========================================================================
    p_dry_run  BOOLEAN := true;  -- SET TO false TO ACTUALLY DELETE

    -- Put your test driver phone numbers here:
    v_phones   TEXT[] := ARRAY[
        '+13060000001',
        '+13060000002'
        -- Add more phone numbers as needed, comma-separated
    ];

    -- =========================================================================
    -- Internal variables (do not edit)
    -- =========================================================================
    v_user_ids      TEXT[];
    v_driver_ids    TEXT[];
    v_ride_ids      TEXT[];
    v_stripe_ids    TEXT[];
    v_wallet_ids    TEXT[];
    v_ai_conv_ids   TEXT[];
    v_corp_member_ids TEXT[];
    v_fare_split_ids  TEXT[];

    v_uid           TEXT;
    v_did           TEXT;
    v_rid           TEXT;
    v_phone         TEXT;

    v_count         INTEGER;
    v_total_deleted INTEGER := 0;
BEGIN
    RAISE NOTICE '============================================================';
    RAISE NOTICE 'TEST DRIVER ACCOUNT DELETION — % mode',
        CASE WHEN p_dry_run THEN 'DRY RUN' ELSE '*** LIVE DELETE ***' END;
    RAISE NOTICE '============================================================';
    RAISE NOTICE 'Phone numbers: %', v_phones;
    RAISE NOTICE '';

    -- =====================================================================
    -- STEP 0: Resolve phone numbers → user_ids → driver_ids
    -- =====================================================================
    SELECT COALESCE(array_agg(u.id), '{}')
    INTO v_user_ids
    FROM users u
    WHERE u.phone = ANY(v_phones);

    IF array_length(v_user_ids, 1) IS NULL OR array_length(v_user_ids, 1) = 0 THEN
        RAISE NOTICE 'No users found for the given phone numbers. Exiting.';
        RETURN;
    END IF;

    RAISE NOTICE 'Found % user(s): %', array_length(v_user_ids, 1), v_user_ids;

    SELECT COALESCE(array_agg(d.id), '{}')
    INTO v_driver_ids
    FROM drivers d
    WHERE d.user_id::text = ANY(v_user_ids);

    RAISE NOTICE 'Found % driver(s): %', COALESCE(array_length(v_driver_ids, 1), 0), v_driver_ids;

    -- Collect Stripe account IDs for manual cleanup notice
    SELECT COALESCE(array_agg(d.stripe_account_id), '{}')
    INTO v_stripe_ids
    FROM drivers d
    WHERE d.id::text = ANY(v_driver_ids)
      AND d.stripe_account_id IS NOT NULL;

    IF array_length(v_stripe_ids, 1) > 0 THEN
        RAISE NOTICE '';
        RAISE NOTICE '*** STRIPE CONNECT ACCOUNTS TO DELETE MANUALLY: %', v_stripe_ids;
        RAISE NOTICE '    Delete these via Stripe Dashboard or API after this script runs.';
    END IF;

    -- Collect ride IDs where this driver was assigned
    SELECT COALESCE(array_agg(r.id), '{}')
    INTO v_ride_ids
    FROM rides r
    WHERE r.driver_id::text = ANY(v_driver_ids);

    RAISE NOTICE 'Found % ride(s) as driver: %', COALESCE(array_length(v_ride_ids, 1), 0), v_ride_ids;

    -- Also collect rides where the user was the rider (test accounts might
    -- have ridden too)
    SELECT COALESCE(array_agg(r.id), '{}')
    INTO v_ride_ids
    FROM rides r
    WHERE r.driver_id::text = ANY(v_driver_ids)
       OR r.rider_id::text = ANY(v_user_ids);

    RAISE NOTICE 'Total rides (as driver or rider): %', COALESCE(array_length(v_ride_ids, 1), 0);

    -- Collect wallet IDs
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'wallets') THEN
        SELECT COALESCE(array_agg(w.id::text), '{}')
        INTO v_wallet_ids
        FROM wallets w
        WHERE w.user_id::text = ANY(v_user_ids);
    ELSE
        v_wallet_ids := '{}';
    END IF;

    -- Collect AI conversation IDs
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ai_conversations') THEN
        SELECT COALESCE(array_agg(ac.id::text), '{}')
        INTO v_ai_conv_ids
        FROM ai_conversations ac
        WHERE ac.user_id::text = ANY(v_user_ids);
    ELSE
        v_ai_conv_ids := '{}';
    END IF;

    -- Collect corporate member IDs
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'corporate_members') THEN
        SELECT COALESCE(array_agg(cm.id::text), '{}')
        INTO v_corp_member_ids
        FROM corporate_members cm
        WHERE cm.user_id::text = ANY(v_user_ids);
    ELSE
        v_corp_member_ids := '{}';
    END IF;

    -- Collect fare split IDs
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'fare_splits') THEN
        SELECT COALESCE(array_agg(fs.id::text), '{}')
        INTO v_fare_split_ids
        FROM fare_splits fs
        WHERE fs.ride_id::text = ANY(v_ride_ids)
           OR fs.requester_id::text = ANY(v_user_ids);
    ELSE
        v_fare_split_ids := '{}';
    END IF;

    RAISE NOTICE '';
    RAISE NOTICE '------------------------------------------------------------';
    RAISE NOTICE 'Beginning deletion in dependency order...';
    RAISE NOTICE '------------------------------------------------------------';

    IF NOT p_dry_run THEN
        -- =================================================================
        -- Enable delete gates for append-only tables
        -- =================================================================
        PERFORM set_config('spinr.financial_events.allow_delete', 'true', true);
        PERFORM set_config('spinr.audit_logs.allow_delete', 'true', true);
        PERFORM set_config('spinr.compliance_export_events.allow_delete', 'true', true);

        -- Temporarily disable triggers that have no GUC gate
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_insurance_periods') THEN
            ALTER TABLE driver_insurance_periods
                DISABLE TRIGGER driver_insurance_periods_no_mutate;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_period_distances') THEN
            ALTER TABLE driver_period_distances
                DISABLE TRIGGER driver_period_distances_no_mutate;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'disputes') THEN
            ALTER TABLE disputes
                DISABLE TRIGGER disputes_no_delete;
        END IF;
    END IF;

    -- =====================================================================
    -- GROUP 1: Ride-related child tables (FK → rides)
    -- =====================================================================

    -- ride_routes (CASCADE from rides, but explicit is safer)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_routes') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_routes WHERE ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM ride_routes WHERE ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_routes: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_routes: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ride_offers
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_offers') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_offers
            WHERE ride_id::text = ANY(v_ride_ids) OR driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM ride_offers
            WHERE ride_id::text = ANY(v_ride_ids) OR driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_offers: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_offers: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ride_messages
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_messages') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_messages
            WHERE ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM ride_messages
            WHERE ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_messages: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_messages: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ride_payment_sources
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_payment_sources') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_payment_sources WHERE ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM ride_payment_sources WHERE ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_payment_sources: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_payment_sources: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ride_live_activities
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_live_activities') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_live_activities
            WHERE ride_id::text = ANY(v_ride_ids) OR rider_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM ride_live_activities
            WHERE ride_id::text = ANY(v_ride_ids) OR rider_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_live_activities: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_live_activities: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ride_route_snapshot_objects
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_route_snapshot_objects') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_route_snapshot_objects WHERE ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM ride_route_snapshot_objects WHERE ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_route_snapshot_objects: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_route_snapshot_objects: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ride_location_gap_events
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_location_gap_events') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_location_gap_events
            WHERE ride_id::text = ANY(v_ride_ids) OR driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM ride_location_gap_events
            WHERE ride_id::text = ANY(v_ride_ids) OR driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_location_gap_events: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_location_gap_events: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ride_distance_recomputes
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_distance_recomputes') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_distance_recomputes WHERE ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM ride_distance_recomputes WHERE ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_distance_recomputes: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_distance_recomputes: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ride_distance_integrity_events
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_distance_integrity_events') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_distance_integrity_events WHERE ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM ride_distance_integrity_events WHERE ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_distance_integrity_events: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_distance_integrity_events: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ride_incentive_claims
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ride_incentive_claims') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ride_incentive_claims
            WHERE ride_id::text = ANY(v_ride_ids) OR driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM ride_incentive_claims
            WHERE ride_id::text = ANY(v_ride_ids) OR driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ride_incentive_claims: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ride_incentive_claims: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- corporate_rides (may not exist in every environment)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'corporate_rides') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM corporate_rides WHERE ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM corporate_rides WHERE ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  corporate_rides: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  corporate_rides: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- stripe_disputes (FK → rides)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'stripe_disputes') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM stripe_disputes WHERE ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM stripe_disputes WHERE ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  stripe_disputes: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  stripe_disputes: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 2: Driver-specific tables
    -- =====================================================================

    -- driver_documents (CASCADE from drivers, but explicit)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_documents') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_documents WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_documents WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_documents: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_documents: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- documents (legacy, no cascade)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'documents') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM documents WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM documents WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  documents: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  documents: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_location_history
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_location_history') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_location_history WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_location_history WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_location_history: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_location_history: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_daily_stats
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_daily_stats') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_daily_stats WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_daily_stats WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_daily_stats: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_daily_stats: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_activity_log
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_activity_log') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_activity_log WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_activity_log WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_activity_log: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_activity_log: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_notes
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_notes') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_notes WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_notes WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_notes: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_notes: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_insurance_periods (trigger disabled above)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_insurance_periods') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_insurance_periods WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_insurance_periods WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_insurance_periods: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_insurance_periods: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_period_distances (trigger disabled above)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_period_distances') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_period_distances WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_period_distances WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_period_distances: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_period_distances: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_vehicle_history
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_vehicle_history') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_vehicle_history WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_vehicle_history WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_vehicle_history: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_vehicle_history: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_onboarding_reminder_log (CASCADE from drivers, but explicit)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_onboarding_reminder_log') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_onboarding_reminder_log
            WHERE driver_id::text = ANY(v_driver_ids) OR user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM driver_onboarding_reminder_log
            WHERE driver_id::text = ANY(v_driver_ids) OR user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_onboarding_reminder_log: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_onboarding_reminder_log: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_statements (CASCADE from drivers)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_statements') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_statements WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_statements WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_statements: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_statements: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_bonuses
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_bonuses') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_bonuses
            WHERE driver_id::text = ANY(v_driver_ids) OR user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM driver_bonuses
            WHERE driver_id::text = ANY(v_driver_ids) OR user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_bonuses: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_bonuses: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_stripe_payouts
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_stripe_payouts') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_stripe_payouts WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_stripe_payouts WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_stripe_payouts: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_stripe_payouts: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_stripe_ledger
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_stripe_ledger') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_stripe_ledger WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_stripe_ledger WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_stripe_ledger: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_stripe_ledger: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 3: Subscription & payout tables
    -- =====================================================================

    -- subscription_payments
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'subscription_payments') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM subscription_payments WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM subscription_payments WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  subscription_payments: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  subscription_payments: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- driver_subscriptions
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_subscriptions') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM driver_subscriptions WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM driver_subscriptions WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  driver_subscriptions: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  driver_subscriptions: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- payouts
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'payouts') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM payouts WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM payouts WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  payouts: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  payouts: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- bank_accounts
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'bank_accounts') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM bank_accounts WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM bank_accounts WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  bank_accounts: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  bank_accounts: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 4: Financial / ledger tables (GUC gates enabled above)
    -- =====================================================================

    -- financial_event_entries (CASCADE from financial_events, but delete
    -- explicitly first to be safe)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM financial_event_entries fee
        WHERE fee.event_id IN (
            SELECT fe.id FROM financial_events fe WHERE fe.user_id::text = ANY(v_user_ids)
        );
    ELSE
        DELETE FROM financial_event_entries
        WHERE event_id IN (
            SELECT id FROM financial_events WHERE user_id::text = ANY(v_user_ids)
        );
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  financial_event_entries: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- financial_events (GUC gate set above)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM financial_events WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM financial_events WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  financial_events: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- Also delete financial_events linked to rides
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM financial_events
        WHERE ride_id::text = ANY(v_ride_ids) AND user_id::text != ALL(COALESCE(v_user_ids, '{}'));
    ELSE
        DELETE FROM financial_event_entries
        WHERE event_id IN (
            SELECT id FROM financial_events
            WHERE ride_id::text = ANY(v_ride_ids) AND user_id::text != ALL(COALESCE(v_user_ids, '{}'))
        );
        DELETE FROM financial_events
        WHERE ride_id::text = ANY(v_ride_ids) AND user_id::text != ALL(COALESCE(v_user_ids, '{}'));
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  financial_events (ride-linked, other users): %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 5: Quest / incentive / loyalty / referral tables
    -- =====================================================================

    -- quest_progress
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'quest_progress') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM quest_progress WHERE driver_id::text = ANY(v_driver_ids);
        ELSE
            DELETE FROM quest_progress WHERE driver_id::text = ANY(v_driver_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  quest_progress: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  quest_progress: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- loyalty_transactions
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'loyalty_transactions') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM loyalty_transactions WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM loyalty_transactions WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  loyalty_transactions: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  loyalty_transactions: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- loyalty_accounts
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'loyalty_accounts') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM loyalty_accounts WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM loyalty_accounts WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  loyalty_accounts: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  loyalty_accounts: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- referral_payouts
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'referral_payouts') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM referral_payouts
            WHERE referrer_user_id::text = ANY(v_user_ids) OR referee_user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM referral_payouts
            WHERE referrer_user_id::text = ANY(v_user_ids) OR referee_user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  referral_payouts: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  referral_payouts: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- promo_applications
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'promo_applications') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM promo_applications WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM promo_applications WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  promo_applications: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  promo_applications: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- promo_user_redemptions
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'promo_user_redemptions') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM promo_user_redemptions WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM promo_user_redemptions WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  promo_user_redemptions: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  promo_user_redemptions: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 6: Wallet tables
    -- =====================================================================

    -- fare_split_participants
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'fare_split_participants') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM fare_split_participants
            WHERE fare_split_id::text = ANY(v_fare_split_ids) OR user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM fare_split_participants
            WHERE fare_split_id::text = ANY(v_fare_split_ids) OR user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  fare_split_participants: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  fare_split_participants: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- fare_splits
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'fare_splits') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM fare_splits
            WHERE id::text = ANY(v_fare_split_ids);
        ELSE
            DELETE FROM fare_splits WHERE id::text = ANY(v_fare_split_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  fare_splits: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  fare_splits: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- wallet_transactions (CASCADE from wallets)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'wallet_transactions') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM wallet_transactions WHERE wallet_id::text = ANY(v_wallet_ids);
        ELSE
            DELETE FROM wallet_transactions WHERE wallet_id::text = ANY(v_wallet_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  wallet_transactions: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  wallet_transactions: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- wallets
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'wallets') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM wallets WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM wallets WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  wallets: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  wallets: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 7: Notification / push / AI tables
    -- =====================================================================

    -- notifications (CASCADE from users)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'notifications') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM notifications WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM notifications WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  notifications: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  notifications: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- notification_preferences
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'notification_preferences') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM notification_preferences WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM notification_preferences WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  notification_preferences: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  notification_preferences: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- push_tokens
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'push_tokens') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM push_tokens WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM push_tokens WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  push_tokens: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  push_tokens: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- push_retry_queue
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'push_retry_queue') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM push_retry_queue WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM push_retry_queue WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  push_retry_queue: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  push_retry_queue: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ai_messages (CASCADE from ai_conversations)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ai_messages') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ai_messages WHERE conversation_id::text = ANY(v_ai_conv_ids);
        ELSE
            DELETE FROM ai_messages WHERE conversation_id::text = ANY(v_ai_conv_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ai_messages: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ai_messages: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- ai_conversations
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ai_conversations') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM ai_conversations WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM ai_conversations WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  ai_conversations: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  ai_conversations: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 8: Complaints / flags / disputes / safety / lost-and-found
    -- =====================================================================

    -- complaints
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'complaints') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM complaints
            WHERE reporter_id::text = ANY(v_user_ids) OR reported_id::text = ANY(v_user_ids)
               OR ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM complaints
            WHERE reporter_id::text = ANY(v_user_ids) OR reported_id::text = ANY(v_user_ids)
               OR ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  complaints: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  complaints: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- flags
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'flags') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM flags
            WHERE target_id::text = ANY(v_user_ids) OR target_id::text = ANY(v_driver_ids)
               OR ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM flags
            WHERE target_id::text = ANY(v_user_ids) OR target_id::text = ANY(v_driver_ids)
               OR ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  flags: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  flags: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- disputes (trigger disabled above)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'disputes') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM disputes
            WHERE reporter_id::text = ANY(v_user_ids) OR ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM disputes
            WHERE reporter_id::text = ANY(v_user_ids) OR ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  disputes: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  disputes: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- lost_and_found_messages
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'lost_and_found_messages') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM lost_and_found_messages
            WHERE sender_id::text = ANY(v_user_ids)
               OR lost_and_found_id::text IN (
                   SELECT id::text FROM lost_and_found
                   WHERE reporter_id::text = ANY(v_user_ids) OR ride_id::text = ANY(v_ride_ids)
               );
        ELSE
            DELETE FROM lost_and_found_messages
            WHERE sender_id::text = ANY(v_user_ids)
               OR lost_and_found_id::text IN (
                   SELECT id::text FROM lost_and_found
                   WHERE reporter_id::text = ANY(v_user_ids) OR ride_id::text = ANY(v_ride_ids)
               );
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  lost_and_found_messages: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  lost_and_found_messages: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- lost_and_found
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'lost_and_found') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM lost_and_found
            WHERE reporter_id::text = ANY(v_user_ids) OR ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM lost_and_found
            WHERE reporter_id::text = ANY(v_user_ids) OR ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  lost_and_found: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  lost_and_found: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- safety_incidents
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'safety_incidents') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM safety_incidents
            WHERE reported_by_user_id::text = ANY(v_user_ids) OR ride_id::text = ANY(v_ride_ids);
        ELSE
            DELETE FROM safety_incidents
            WHERE reported_by_user_id::text = ANY(v_user_ids) OR ride_id::text = ANY(v_ride_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  safety_incidents: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  safety_incidents: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 9: Corporate membership (if test driver was a corp member)
    -- =====================================================================

    -- corporate_member_allowances
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'corporate_member_allowances') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM corporate_member_allowances
            WHERE member_id::text = ANY(v_corp_member_ids);
        ELSE
            DELETE FROM corporate_member_allowances WHERE member_id::text = ANY(v_corp_member_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  corporate_member_allowances: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  corporate_member_allowances: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- corporate_allowance_requests
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'corporate_allowance_requests') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM corporate_allowance_requests
            WHERE member_id::text = ANY(v_corp_member_ids);
        ELSE
            DELETE FROM corporate_allowance_requests WHERE member_id::text = ANY(v_corp_member_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  corporate_allowance_requests: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  corporate_allowance_requests: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- corporate_members
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'corporate_members') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM corporate_members WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM corporate_members WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  corporate_members: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  corporate_members: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 10: Support / marketing / misc user tables
    -- =====================================================================

    -- support_tickets
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'support_tickets') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM support_tickets WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM support_tickets WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  support_tickets: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  support_tickets: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- saved_addresses
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'saved_addresses') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM saved_addresses WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM saved_addresses WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  saved_addresses: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  saved_addresses: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- emergency_contacts
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'emergency_contacts') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM emergency_contacts WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM emergency_contacts WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  emergency_contacts: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  emergency_contacts: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- marketing_preferences (no FK by design, but clean up)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'marketing_preferences') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM marketing_preferences WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM marketing_preferences WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  marketing_preferences: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  marketing_preferences: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- marketing_consent_events (no FK by design)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'marketing_consent_events') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM marketing_consent_events WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM marketing_consent_events WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  marketing_consent_events: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  marketing_consent_events: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- email_send_log
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'email_send_log') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM email_send_log WHERE recipient_user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM email_send_log WHERE recipient_user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  email_send_log: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  email_send_log: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- price_searches
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'price_searches') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM price_searches WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM price_searches WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  price_searches: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  price_searches: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- data_export_requests
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'data_export_requests') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM data_export_requests WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM data_export_requests WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  data_export_requests: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  data_export_requests: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 11: Auth tables
    -- =====================================================================

    -- refresh_tokens
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'refresh_tokens') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM refresh_tokens WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM refresh_tokens WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  refresh_tokens: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  refresh_tokens: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- otp_records
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'otp_records') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM otp_records WHERE phone = ANY(v_phones);
        ELSE
            DELETE FROM otp_records WHERE phone = ANY(v_phones);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  otp_records: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  otp_records: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- rider_email_verification_otp
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'rider_email_verification_otp') THEN
        IF p_dry_run THEN
            SELECT COUNT(*) INTO v_count FROM rider_email_verification_otp
            WHERE user_id::text = ANY(v_user_ids);
        ELSE
            DELETE FROM rider_email_verification_otp WHERE user_id::text = ANY(v_user_ids);
            GET DIAGNOSTICS v_count = ROW_COUNT;
        END IF;
        RAISE NOTICE '  rider_email_verification_otp: %', v_count;
        v_total_deleted := v_total_deleted + v_count;
    ELSE
        RAISE NOTICE '  rider_email_verification_otp: TABLE DOES NOT EXIST — skipped';
    END IF;

    -- =====================================================================
    -- GROUP 12: Audit logs (GUC gate set above)
    -- =====================================================================

    -- audit_logs (related to these users)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM audit_logs
        WHERE actor_id::text = ANY(v_user_ids)
           OR entity_id::text = ANY(v_user_ids)
           OR entity_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM audit_logs
        WHERE actor_id::text = ANY(v_user_ids)
           OR entity_id::text = ANY(v_user_ids)
           OR entity_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  audit_logs: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 13: Nullify references that use SET NULL on delete
    -- =====================================================================

    -- reconciliation_discrepancies (resolved_by → user)
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'reconciliation_discrepancies') THEN
        IF NOT p_dry_run THEN
            UPDATE reconciliation_discrepancies SET resolved_by = NULL
            WHERE resolved_by::text = ANY(v_user_ids);
        END IF;
    END IF;

    -- =====================================================================
    -- GROUP 14: Delete rides (children already deleted above)
    -- =====================================================================
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM rides WHERE id = ANY(v_ride_ids);
    ELSE
        DELETE FROM rides WHERE id = ANY(v_ride_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  rides: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 15: Delete driver record (children already deleted above)
    -- =====================================================================
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM drivers WHERE id = ANY(v_driver_ids);
    ELSE
        DELETE FROM drivers WHERE id = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  drivers: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 16: Delete user record (the root)
    -- =====================================================================
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM users WHERE id = ANY(v_user_ids);
    ELSE
        DELETE FROM users WHERE id = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  users: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- Re-enable append-only triggers
    -- =====================================================================
    IF NOT p_dry_run THEN
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_insurance_periods') THEN
            ALTER TABLE driver_insurance_periods
                ENABLE TRIGGER driver_insurance_periods_no_mutate;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'driver_period_distances') THEN
            ALTER TABLE driver_period_distances
                ENABLE TRIGGER driver_period_distances_no_mutate;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'disputes') THEN
            ALTER TABLE disputes
                ENABLE TRIGGER disputes_no_delete;
        END IF;

        PERFORM set_config('spinr.financial_events.allow_delete', 'false', true);
        PERFORM set_config('spinr.audit_logs.allow_delete', 'false', true);
        PERFORM set_config('spinr.compliance_export_events.allow_delete', 'false', true);
    END IF;

    -- =====================================================================
    -- SUMMARY
    -- =====================================================================
    RAISE NOTICE '';
    RAISE NOTICE '============================================================';
    RAISE NOTICE 'COMPLETE — % mode', CASE WHEN p_dry_run THEN 'DRY RUN' ELSE 'LIVE' END;
    RAISE NOTICE 'Total rows affected: %', v_total_deleted;
    IF array_length(v_stripe_ids, 1) > 0 THEN
        RAISE NOTICE '';
        RAISE NOTICE 'REMINDER: Delete these Stripe Connect accounts manually:';
        FOREACH v_uid IN ARRAY v_stripe_ids LOOP
            RAISE NOTICE '  - %', v_uid;
        END LOOP;
    END IF;
    RAISE NOTICE '============================================================';
END;
$$;
