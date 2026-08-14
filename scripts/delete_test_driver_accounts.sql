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
    SELECT COALESCE(array_agg(w.id::text), '{}')
    INTO v_wallet_ids
    FROM wallets w
    WHERE w.user_id::text = ANY(v_user_ids);

    -- Collect AI conversation IDs
    SELECT COALESCE(array_agg(ac.id::text), '{}')
    INTO v_ai_conv_ids
    FROM ai_conversations ac
    WHERE ac.user_id::text = ANY(v_user_ids);

    -- Collect corporate member IDs
    SELECT COALESCE(array_agg(cm.id::text), '{}')
    INTO v_corp_member_ids
    FROM corporate_members cm
    WHERE cm.user_id::text = ANY(v_user_ids);

    -- Collect fare split IDs
    SELECT COALESCE(array_agg(fs.id::text), '{}')
    INTO v_fare_split_ids
    FROM fare_splits fs
    WHERE fs.ride_id::text = ANY(v_ride_ids)
       OR fs.requester_id::text = ANY(v_user_ids);

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
        ALTER TABLE driver_insurance_periods
            DISABLE TRIGGER driver_insurance_periods_no_mutate;
        ALTER TABLE driver_period_distances
            DISABLE TRIGGER driver_period_distances_no_mutate;
        ALTER TABLE disputes
            DISABLE TRIGGER disputes_no_delete;
    END IF;

    -- =====================================================================
    -- GROUP 1: Ride-related child tables (FK → rides)
    -- =====================================================================

    -- ride_routes (CASCADE from rides, but explicit is safer)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM ride_routes WHERE ride_id::text = ANY(v_ride_ids);
    ELSE
        DELETE FROM ride_routes WHERE ride_id::text = ANY(v_ride_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  ride_routes: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- ride_offers
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

    -- ride_messages
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

    -- ride_payment_sources
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM ride_payment_sources WHERE ride_id::text = ANY(v_ride_ids);
    ELSE
        DELETE FROM ride_payment_sources WHERE ride_id::text = ANY(v_ride_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  ride_payment_sources: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- ride_live_activities
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

    -- ride_route_snapshot_objects
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM ride_route_snapshot_objects WHERE ride_id::text = ANY(v_ride_ids);
    ELSE
        DELETE FROM ride_route_snapshot_objects WHERE ride_id::text = ANY(v_ride_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  ride_route_snapshot_objects: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- ride_location_gap_events
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

    -- ride_distance_recomputes
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM ride_distance_recomputes WHERE ride_id::text = ANY(v_ride_ids);
    ELSE
        DELETE FROM ride_distance_recomputes WHERE ride_id::text = ANY(v_ride_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  ride_distance_recomputes: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- ride_distance_integrity_events
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM ride_distance_integrity_events WHERE ride_id::text = ANY(v_ride_ids);
    ELSE
        DELETE FROM ride_distance_integrity_events WHERE ride_id::text = ANY(v_ride_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  ride_distance_integrity_events: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- ride_incentive_claims
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

    -- corporate_rides
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM corporate_rides WHERE ride_id::text = ANY(v_ride_ids);
    ELSE
        DELETE FROM corporate_rides WHERE ride_id::text = ANY(v_ride_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  corporate_rides: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- stripe_disputes (FK → rides)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM stripe_disputes WHERE ride_id::text = ANY(v_ride_ids);
    ELSE
        DELETE FROM stripe_disputes WHERE ride_id::text = ANY(v_ride_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  stripe_disputes: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 2: Driver-specific tables
    -- =====================================================================

    -- driver_documents (CASCADE from drivers, but explicit)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_documents WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_documents WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_documents: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- documents (legacy, no cascade)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM documents WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM documents WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  documents: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_location_history
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_location_history WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_location_history WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_location_history: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_daily_stats
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_daily_stats WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_daily_stats WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_daily_stats: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_activity_log
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_activity_log WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_activity_log WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_activity_log: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_notes
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_notes WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_notes WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_notes: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_insurance_periods (trigger disabled above)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_insurance_periods WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_insurance_periods WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_insurance_periods: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_period_distances (trigger disabled above)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_period_distances WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_period_distances WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_period_distances: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_vehicle_history
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_vehicle_history WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_vehicle_history WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_vehicle_history: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_onboarding_reminder_log (CASCADE from drivers, but explicit)
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

    -- driver_statements (CASCADE from drivers)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_statements WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_statements WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_statements: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_bonuses
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

    -- driver_stripe_payouts
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_stripe_payouts WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_stripe_payouts WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_stripe_payouts: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_stripe_ledger
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_stripe_ledger WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_stripe_ledger WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_stripe_ledger: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 3: Subscription & payout tables
    -- =====================================================================

    -- subscription_payments
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM subscription_payments WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM subscription_payments WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  subscription_payments: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- driver_subscriptions
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM driver_subscriptions WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM driver_subscriptions WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  driver_subscriptions: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- payouts
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM payouts WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM payouts WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  payouts: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- bank_accounts
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM bank_accounts WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM bank_accounts WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  bank_accounts: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

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
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM quest_progress WHERE driver_id::text = ANY(v_driver_ids);
    ELSE
        DELETE FROM quest_progress WHERE driver_id::text = ANY(v_driver_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  quest_progress: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- loyalty_transactions
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM loyalty_transactions WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM loyalty_transactions WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  loyalty_transactions: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- loyalty_accounts
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM loyalty_accounts WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM loyalty_accounts WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  loyalty_accounts: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- referral_payouts
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

    -- promo_applications
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM promo_applications WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM promo_applications WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  promo_applications: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- promo_user_redemptions
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM promo_user_redemptions WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM promo_user_redemptions WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  promo_user_redemptions: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 6: Wallet tables
    -- =====================================================================

    -- fare_split_participants
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

    -- fare_splits
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM fare_splits
        WHERE id::text = ANY(v_fare_split_ids);
    ELSE
        DELETE FROM fare_splits WHERE id::text = ANY(v_fare_split_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  fare_splits: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- wallet_transactions (CASCADE from wallets)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM wallet_transactions WHERE wallet_id::text = ANY(v_wallet_ids);
    ELSE
        DELETE FROM wallet_transactions WHERE wallet_id::text = ANY(v_wallet_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  wallet_transactions: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- wallets
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM wallets WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM wallets WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  wallets: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 7: Notification / push / AI tables
    -- =====================================================================

    -- notifications (CASCADE from users)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM notifications WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM notifications WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  notifications: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- notification_preferences
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM notification_preferences WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM notification_preferences WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  notification_preferences: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- push_tokens
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM push_tokens WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM push_tokens WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  push_tokens: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- push_retry_queue
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM push_retry_queue WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM push_retry_queue WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  push_retry_queue: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- ai_messages (CASCADE from ai_conversations)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM ai_messages WHERE conversation_id::text = ANY(v_ai_conv_ids);
    ELSE
        DELETE FROM ai_messages WHERE conversation_id::text = ANY(v_ai_conv_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  ai_messages: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- ai_conversations
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM ai_conversations WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM ai_conversations WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  ai_conversations: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 8: Complaints / flags / disputes / safety / lost-and-found
    -- =====================================================================

    -- complaints
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

    -- flags
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

    -- disputes (trigger disabled above)
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

    -- lost_and_found_messages
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

    -- lost_and_found
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

    -- safety_incidents
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

    -- =====================================================================
    -- GROUP 9: Corporate membership (if test driver was a corp member)
    -- =====================================================================

    -- corporate_member_allowances
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM corporate_member_allowances
        WHERE member_id::text = ANY(v_corp_member_ids);
    ELSE
        DELETE FROM corporate_member_allowances WHERE member_id::text = ANY(v_corp_member_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  corporate_member_allowances: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- corporate_allowance_requests
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM corporate_allowance_requests
        WHERE member_id::text = ANY(v_corp_member_ids);
    ELSE
        DELETE FROM corporate_allowance_requests WHERE member_id::text = ANY(v_corp_member_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  corporate_allowance_requests: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- corporate_members
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM corporate_members WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM corporate_members WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  corporate_members: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 10: Support / marketing / misc user tables
    -- =====================================================================

    -- support_tickets
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM support_tickets WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM support_tickets WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  support_tickets: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- saved_addresses
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM saved_addresses WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM saved_addresses WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  saved_addresses: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- emergency_contacts
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM emergency_contacts WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM emergency_contacts WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  emergency_contacts: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- marketing_preferences (no FK by design, but clean up)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM marketing_preferences WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM marketing_preferences WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  marketing_preferences: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- marketing_consent_events (no FK by design)
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM marketing_consent_events WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM marketing_consent_events WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  marketing_consent_events: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- email_send_log
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM email_send_log WHERE recipient_user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM email_send_log WHERE recipient_user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  email_send_log: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- price_searches
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM price_searches WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM price_searches WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  price_searches: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- data_export_requests
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM data_export_requests WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM data_export_requests WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  data_export_requests: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- =====================================================================
    -- GROUP 11: Auth tables
    -- =====================================================================

    -- refresh_tokens
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM refresh_tokens WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM refresh_tokens WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  refresh_tokens: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- otp_records
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM otp_records WHERE phone = ANY(v_phones);
    ELSE
        DELETE FROM otp_records WHERE phone = ANY(v_phones);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  otp_records: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

    -- rider_email_verification_otp
    IF p_dry_run THEN
        SELECT COUNT(*) INTO v_count FROM rider_email_verification_otp
        WHERE user_id::text = ANY(v_user_ids);
    ELSE
        DELETE FROM rider_email_verification_otp WHERE user_id::text = ANY(v_user_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RAISE NOTICE '  rider_email_verification_otp: %', v_count;
    v_total_deleted := v_total_deleted + v_count;

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
    IF NOT p_dry_run THEN
        UPDATE reconciliation_discrepancies SET resolved_by = NULL
        WHERE resolved_by::text = ANY(v_user_ids);
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
        ALTER TABLE driver_insurance_periods
            ENABLE TRIGGER driver_insurance_periods_no_mutate;
        ALTER TABLE driver_period_distances
            ENABLE TRIGGER driver_period_distances_no_mutate;
        ALTER TABLE disputes
            ENABLE TRIGGER disputes_no_delete;

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
