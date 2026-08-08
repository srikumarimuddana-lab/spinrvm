-- verify_migrations_286_289.sql
--
-- Runtime verification for migrations 286-289 (ledger durability, double-entry
-- legs, atomic card settlement, DSAR purge delete gate).
--
-- WHY THIS EXISTS
--   Those four migrations were authored in an environment with no reachable
--   Postgres. They were validated with pglast (the real PostgreSQL parser),
--   which proves SYNTAX ONLY — not that the FK targets exist, that the CHECK
--   constraints reject what they should, that the triggers fire, that the GUC
--   gate actually gates, or that the RPCs behave. This script closes that gap.
--
-- ============================================================================
-- SAFETY
--   * Run on STAGING (or any throwaway copy). Do NOT run against production.
--   * The whole script runs inside ONE transaction and ends in ROLLBACK, so it
--     leaves nothing behind even on success. Nothing is committed. Ever.
--   * It does briefly take row locks on one existing user/ride while the
--     transaction is open. On a quiet staging DB that is a non-event; on a busy
--     one, run it off-peak.
--   * It does NOT create users or rides (their NOT NULL surface is large and
--     version-dependent). It borrows one existing row of each, read-only apart
--     from the rolled-back writes.
--
-- PREREQUISITE
--   Migrations 286-289 must already be applied:
--       cd backend && python scripts/migrate.py          # or --dry-run first
--
-- RUN
--       psql "$PG_CONNECTION_STRING" -v ON_ERROR_STOP=1 \
--            -f backend/scripts/verify_migrations_286_289.sql
--
-- READ THE OUTPUT
--   Every check prints "PASS: ..." or "FAIL: ...". The final block prints a
--   summary and raises if anything failed, so a non-zero psql exit == failure.
--   Checks that cannot run (e.g. no completed ride available) print "SKIP: ..."
--   with the reason — a SKIP is not a pass, please report it back.
-- ============================================================================

\set ON_ERROR_STOP on
\timing off

BEGIN;

CREATE TEMP TABLE _v(check_name text, status text, detail text) ON COMMIT DROP;

CREATE OR REPLACE FUNCTION pg_temp._ok(p_name text, p_detail text DEFAULT '')
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO _v VALUES (p_name, 'PASS', p_detail);
    RAISE NOTICE 'PASS: % %', p_name, p_detail;
END; $$;

CREATE OR REPLACE FUNCTION pg_temp._bad(p_name text, p_detail text DEFAULT '')
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO _v VALUES (p_name, 'FAIL', p_detail);
    RAISE WARNING 'FAIL: % %', p_name, p_detail;
END; $$;

CREATE OR REPLACE FUNCTION pg_temp._skip(p_name text, p_detail text DEFAULT '')
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO _v VALUES (p_name, 'SKIP', p_detail);
    RAISE NOTICE 'SKIP: % (%)', p_name, p_detail;
END; $$;


-- ===========================================================================
-- 0. Objects exist at all
-- ===========================================================================
DO $$
BEGIN
    IF to_regclass('public.financial_event_entries') IS NOT NULL
        THEN PERFORM pg_temp._ok('286 table exists');
        ELSE PERFORM pg_temp._bad('286 table exists', 'financial_event_entries missing — is 286 applied?');
    END IF;

    IF to_regclass('public.financial_event_entries_unbalanced') IS NOT NULL
        THEN PERFORM pg_temp._ok('286 unbalanced view exists');
        ELSE PERFORM pg_temp._bad('286 unbalanced view exists', 'view missing');
    END IF;

    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'financial_events_missing_legs')
        THEN PERFORM pg_temp._ok('287 RPC exists');
        ELSE PERFORM pg_temp._bad('287 RPC exists', 'financial_events_missing_legs missing');
    END IF;

    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'settle_ride_card_payment')
        THEN PERFORM pg_temp._ok('288 RPC exists');
        ELSE PERFORM pg_temp._bad('288 RPC exists', 'settle_ride_card_payment missing');
    END IF;

    -- 289 must NOT have created a second overload of the trigger fn
    IF (SELECT count(*) FROM pg_proc WHERE proname = '_financial_events_immutable') = 1
        THEN PERFORM pg_temp._ok('289 trigger fn single definition');
        ELSE PERFORM pg_temp._bad('289 trigger fn single definition',
             'expected exactly 1, found ' || (SELECT count(*) FROM pg_proc WHERE proname='_financial_events_immutable'));
    END IF;

    -- the trigger must still be bound to financial_events
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'financial_events_no_mutate'
                 AND tgrelid = 'public.financial_events'::regclass)
        THEN PERFORM pg_temp._ok('289 trigger still bound');
        ELSE PERFORM pg_temp._bad('289 trigger still bound', 'financial_events_no_mutate not found');
    END IF;
END $$;


-- ===========================================================================
-- 1. Grants — the new functions must be service_role only (migration-205 form)
-- ===========================================================================
DO $$
DECLARE r record; leaked text := '';
BEGIN
    FOR r IN
        SELECT p.proname, a.rolname
        FROM pg_proc p
        CROSS JOIN LATERAL (VALUES ('anon'),('authenticated'),('public')) AS t(rolname)
        JOIN pg_roles a ON a.rolname = t.rolname
        WHERE p.proname IN ('financial_events_missing_legs','settle_ride_card_payment','purge_pii_retention')
          AND has_function_privilege(a.rolname, p.oid, 'EXECUTE')
    LOOP
        leaked := leaked || r.proname || '->' || r.rolname || ' ';
    END LOOP;

    IF leaked = ''
        THEN PERFORM pg_temp._ok('grants: anon/authenticated cannot EXECUTE');
        ELSE PERFORM pg_temp._bad('grants: anon/authenticated cannot EXECUTE', 'LEAKED: ' || leaked);
    END IF;

    IF has_function_privilege('service_role', 'financial_events_missing_legs(int)', 'EXECUTE')
        THEN PERFORM pg_temp._ok('grants: service_role CAN execute 287');
        ELSE PERFORM pg_temp._bad('grants: service_role CAN execute 287',
             'service_role lost EXECUTE — the REVOKE stripped inherited rights and the GRANT did not restore it');
    END IF;
EXCEPTION WHEN undefined_object THEN
    PERFORM pg_temp._skip('grants', 'a role (anon/authenticated/service_role) does not exist on this DB');
END $$;


-- ===========================================================================
-- 2. Migration 286 — constraints on financial_event_entries
--    Borrow one existing user + ride so the FKs resolve. All rolled back.
-- ===========================================================================
DO $$
DECLARE
    v_uid   text;
    v_rid   text;
    v_ev    uuid := gen_random_uuid();
    v_ev2   uuid := gen_random_uuid();
    v_n     int;
BEGIN
    SELECT id INTO v_uid FROM users LIMIT 1;
    SELECT id INTO v_rid FROM rides LIMIT 1;

    IF v_uid IS NULL THEN
        PERFORM pg_temp._skip('286 constraints', 'no users row to borrow for the FK');
        RETURN;
    END IF;

    INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at)
    VALUES (v_ev, 'stripe_charge', v_uid, v_rid, 2000, 'pi_verify_1',
            '{"source":"process_payment"}'::jsonb, now() - interval '2 hours');

    -- 2a. a balanced set inserts fine
    INSERT INTO financial_event_entries (event_id, account, side, amount_cents)
    VALUES (v_ev, 'stripe_receivable', 'debit',  2000),
           (v_ev, 'driver_payable',    'credit', 1500),
           (v_ev, 'tax_payable',       'credit',  220),
           (v_ev, 'platform_revenue',  'credit',  280);
    PERFORM pg_temp._ok('286 balanced legs insert');

    -- 2b. UNIQUE(event_id, account, side) must reject a duplicate leg
    BEGIN
        INSERT INTO financial_event_entries (event_id, account, side, amount_cents)
        VALUES (v_ev, 'stripe_receivable', 'debit', 2000);
        PERFORM pg_temp._bad('286 UNIQUE(event,account,side)', 'duplicate leg was ACCEPTED');
    EXCEPTION WHEN unique_violation THEN
        PERFORM pg_temp._ok('286 UNIQUE(event,account,side)', '— duplicate rejected (this is what makes projection retries idempotent)');
    END;

    -- 2c. amount_cents must be > 0
    BEGIN
        INSERT INTO financial_event_entries (event_id, account, side, amount_cents)
        VALUES (v_ev, 'promo_expense', 'debit', 0);
        PERFORM pg_temp._bad('286 CHECK amount_cents > 0', 'zero amount was ACCEPTED');
    EXCEPTION WHEN check_violation THEN
        PERFORM pg_temp._ok('286 CHECK amount_cents > 0');
    END;

    -- 2d. account must be in the chart of accounts
    BEGIN
        INSERT INTO financial_event_entries (event_id, account, side, amount_cents)
        VALUES (v_ev, 'not_a_real_account', 'debit', 1);
        PERFORM pg_temp._bad('286 CHECK account enum', 'unknown account was ACCEPTED');
    EXCEPTION WHEN check_violation THEN
        PERFORM pg_temp._ok('286 CHECK account enum');
    END;

    -- 2e. side must be debit|credit
    BEGIN
        INSERT INTO financial_event_entries (event_id, account, side, amount_cents)
        VALUES (v_ev, 'promo_expense', 'sideways', 1);
        PERFORM pg_temp._bad('286 CHECK side enum', 'bad side was ACCEPTED');
    EXCEPTION WHEN check_violation THEN
        PERFORM pg_temp._ok('286 CHECK side enum');
    END;

    -- 2f. UPDATE must always be blocked (append-only)
    BEGIN
        UPDATE financial_event_entries SET amount_cents = 1 WHERE event_id = v_ev;
        PERFORM pg_temp._bad('286 UPDATE blocked', 'UPDATE was ACCEPTED — entries are not append-only');
    EXCEPTION WHEN OTHERS THEN
        PERFORM pg_temp._ok('286 UPDATE blocked', '(' || SQLSTATE || ')');
    END;

    -- 2g. the unbalanced view must NOT list a balanced event
    SELECT count(*) INTO v_n FROM financial_event_entries_unbalanced WHERE event_id = v_ev;
    IF v_n = 0
        THEN PERFORM pg_temp._ok('286 unbalanced view ignores balanced event');
        ELSE PERFORM pg_temp._bad('286 unbalanced view ignores balanced event', 'view listed a balanced entry');
    END IF;

    -- 2h. ...and MUST list a lopsided one
    INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at)
    VALUES (v_ev2, 'stripe_charge', v_uid, v_rid, 500, 'pi_verify_2', '{}'::jsonb, now() - interval '2 hours');
    INSERT INTO financial_event_entries (event_id, account, side, amount_cents)
    VALUES (v_ev2, 'stripe_receivable', 'debit', 500);   -- no counter-leg

    SELECT count(*) INTO v_n FROM financial_event_entries_unbalanced WHERE event_id = v_ev2;
    IF v_n = 1
        THEN PERFORM pg_temp._ok('286 unbalanced view catches lopsided event');
        ELSE PERFORM pg_temp._bad('286 unbalanced view catches lopsided event', 'expected 1 row, got ' || v_n);
    END IF;

    -- 2i. FK CASCADE: deleting the header must take its legs with it.
    --     Needs the 289 gate, which is also what proves the gate works.
    PERFORM set_config('spinr.financial_events.allow_delete', 'true', true);
    DELETE FROM financial_events WHERE id = v_ev2;
    PERFORM set_config('spinr.financial_events.allow_delete', 'false', true);

    SELECT count(*) INTO v_n FROM financial_event_entries WHERE event_id = v_ev2;
    IF v_n = 0
        THEN PERFORM pg_temp._ok('286 FK ON DELETE CASCADE takes legs with header');
        ELSE PERFORM pg_temp._bad('286 FK ON DELETE CASCADE', v_n || ' orphan legs left behind');
    END IF;
END $$;


-- ===========================================================================
-- 3. Migration 289 — the DELETE gate. Highest-risk item on the branch.
-- ===========================================================================
DO $$
DECLARE
    v_uid text; v_rid text; v_ev uuid := gen_random_uuid(); v_n int;
BEGIN
    SELECT id INTO v_uid FROM users LIMIT 1;
    SELECT id INTO v_rid FROM rides LIMIT 1;
    IF v_uid IS NULL THEN
        PERFORM pg_temp._skip('289 delete gate', 'no users row to borrow');
        RETURN;
    END IF;

    INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at)
    VALUES (v_ev, 'stripe_charge', v_uid, v_rid, 100, 'pi_gate', '{}'::jsonb, now() - interval '2 hours');

    -- 3a. UPDATE is blocked unconditionally, GUC or not
    PERFORM set_config('spinr.financial_events.allow_delete', 'true', true);
    BEGIN
        UPDATE financial_events SET delta_cents = 1 WHERE id = v_ev;
        PERFORM pg_temp._bad('289 UPDATE blocked even WITH gate open',
                             'CRITICAL: the tax ledger is mutable');
    EXCEPTION WHEN OTHERS THEN
        PERFORM pg_temp._ok('289 UPDATE blocked even WITH gate open', '(' || SQLSTATE || ')');
    END;
    PERFORM set_config('spinr.financial_events.allow_delete', 'false', true);

    -- 3b. DELETE is blocked when the gate is shut
    BEGIN
        DELETE FROM financial_events WHERE id = v_ev;
        PERFORM pg_temp._bad('289 DELETE blocked without gate',
                             'CRITICAL: anyone can delete tax-ledger rows');
    EXCEPTION WHEN OTHERS THEN
        PERFORM pg_temp._ok('289 DELETE blocked without gate', '(' || SQLSTATE || ')');
    END;

    -- 3c. DELETE is blocked when the GUC is set to something other than 'true'
    PERFORM set_config('spinr.financial_events.allow_delete', 'yes', true);
    BEGIN
        DELETE FROM financial_events WHERE id = v_ev;
        PERFORM pg_temp._bad('289 DELETE blocked for non-"true" GUC', 'a truthy-looking value opened the gate');
    EXCEPTION WHEN OTHERS THEN
        PERFORM pg_temp._ok('289 DELETE blocked for non-"true" GUC');
    END;
    PERFORM set_config('spinr.financial_events.allow_delete', 'false', true);

    -- 3d. DELETE succeeds with the gate open
    PERFORM set_config('spinr.financial_events.allow_delete', 'true', true);
    DELETE FROM financial_events WHERE id = v_ev;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    PERFORM set_config('spinr.financial_events.allow_delete', 'false', true);
    IF v_n = 1
        THEN PERFORM pg_temp._ok('289 DELETE allowed with gate open');
        ELSE PERFORM pg_temp._bad('289 DELETE allowed with gate open', 'deleted ' || v_n || ' rows');
    END IF;
END $$;

-- 3e. The GUC must be transaction-local, so a nested/aborted block cannot
--     leave the gate open for later statements in the same session.
DO $$
DECLARE v_val text;
BEGIN
    BEGIN
        PERFORM set_config('spinr.financial_events.allow_delete', 'true', true);
        RAISE EXCEPTION 'deliberate abort';
    EXCEPTION WHEN OTHERS THEN
        NULL;   -- subtransaction rolled back
    END;
    v_val := coalesce(current_setting('spinr.financial_events.allow_delete', true), '<unset>');
    IF v_val IS DISTINCT FROM 'true'
        THEN PERFORM pg_temp._ok('289 GUC rolls back with aborted subtransaction', '(now ' || v_val || ')');
        ELSE PERFORM pg_temp._bad('289 GUC rolls back with aborted subtransaction',
             'gate stayed OPEN after an aborted block — a failed purge could leave the ledger deletable');
    END IF;
END $$;


-- ===========================================================================
-- 4. Migration 287 — the projection work queue
-- ===========================================================================
DO $$
DECLARE
    v_uid text; v_rid text;
    v_old uuid := gen_random_uuid();   -- old, projectable        -> SHOULD appear
    v_new uuid := gen_random_uuid();   -- inside 30-min grace     -> should NOT
    v_zero uuid := gen_random_uuid();  -- delta_cents = 0         -> should NOT
    v_wrong uuid := gen_random_uuid(); -- non-projectable type    -> should NOT
    v_done uuid := gen_random_uuid();  -- already has legs        -> should NOT
BEGIN
    SELECT id INTO v_uid FROM users LIMIT 1;
    SELECT id INTO v_rid FROM rides LIMIT 1;
    IF v_uid IS NULL THEN
        PERFORM pg_temp._skip('287 work queue', 'no users row to borrow');
        RETURN;
    END IF;

    INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at) VALUES
      (v_old,  'stripe_charge', v_uid, v_rid, 2000, 'pi_old',  '{}'::jsonb, now() - interval '2 hours'),
      (v_new,  'stripe_charge', v_uid, v_rid, 2000, 'pi_new',  '{}'::jsonb, now() - interval '2 minutes'),
      (v_zero, 'stripe_charge', v_uid, v_rid,    0, 'pi_zero', '{}'::jsonb, now() - interval '2 hours'),
      (v_wrong,'wallet_topup',  v_uid, v_rid, 2000, 'pi_wrng', '{}'::jsonb, now() - interval '2 hours'),
      (v_done, 'stripe_charge', v_uid, v_rid, 2000, 'pi_done', '{}'::jsonb, now() - interval '2 hours');

    INSERT INTO financial_event_entries (event_id, account, side, amount_cents) VALUES
      (v_done, 'stripe_receivable', 'debit',  2000),
      (v_done, 'platform_revenue',  'credit', 2000);

    IF EXISTS (SELECT 1 FROM financial_events_missing_legs(500) WHERE id = v_old)
        THEN PERFORM pg_temp._ok('287 queue INCLUDES an old leg-less charge');
        ELSE PERFORM pg_temp._bad('287 queue INCLUDES an old leg-less charge', 'projection would never see it');
    END IF;

    IF EXISTS (SELECT 1 FROM financial_events_missing_legs(500) WHERE id = v_new)
        THEN PERFORM pg_temp._bad('287 queue EXCLUDES rows inside the 30-min grace',
             'CRITICAL: tip-race window is open — legs could be built from a pre-tip ride row');
        ELSE PERFORM pg_temp._ok('287 queue EXCLUDES rows inside the 30-min grace');
    END IF;

    IF EXISTS (SELECT 1 FROM financial_events_missing_legs(500) WHERE id = v_zero)
        THEN PERFORM pg_temp._bad('287 queue EXCLUDES delta_cents = 0', 'a $0 header would wedge the queue head forever');
        ELSE PERFORM pg_temp._ok('287 queue EXCLUDES delta_cents = 0');
    END IF;

    IF EXISTS (SELECT 1 FROM financial_events_missing_legs(500) WHERE id = v_wrong)
        THEN PERFORM pg_temp._bad('287 queue EXCLUDES non-projectable event_type', 'wallet_topup would wedge the queue');
        ELSE PERFORM pg_temp._ok('287 queue EXCLUDES non-projectable event_type');
    END IF;

    IF EXISTS (SELECT 1 FROM financial_events_missing_legs(500) WHERE id = v_done)
        THEN PERFORM pg_temp._bad('287 queue EXCLUDES already-projected rows', 'anti-join is not working');
        ELSE PERFORM pg_temp._ok('287 queue EXCLUDES already-projected rows');
    END IF;

    -- limit clamp: LEAST(GREATEST(p_limit,1),500)
    IF (SELECT count(*) FROM financial_events_missing_legs(0)) <= 1
        THEN PERFORM pg_temp._ok('287 limit clamps at >= 1');
        ELSE PERFORM pg_temp._bad('287 limit clamps at >= 1');
    END IF;
    IF (SELECT count(*) FROM financial_events_missing_legs(100000)) <= 500
        THEN PERFORM pg_temp._ok('287 limit clamps at <= 500');
        ELSE PERFORM pg_temp._bad('287 limit clamps at <= 500');
    END IF;
END $$;

-- 4b. Does the anti-join seq-scan financial_events at scale?  Advisory only.
DO $$
DECLARE v_plan text;
BEGIN
    EXECUTE 'EXPLAIN (FORMAT TEXT) SELECT * FROM financial_events_missing_legs(200)' INTO v_plan;
    RAISE NOTICE 'INFO (advisory): work-queue plan starts: %', left(v_plan, 200);
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'INFO: could not EXPLAIN the work queue (%).', SQLERRM;
END $$;


-- ===========================================================================
-- 5. Migration 288 — atomic settle. Needs a ride we can safely mutate.
-- ===========================================================================
DO $$
DECLARE
    v_rid   text;
    v_uid   text;
    v_ev    uuid := gen_random_uuid();
    v_ev2   uuid := gen_random_uuid();
    v_ret   text;
    v_stat  text;
    v_tip   numeric;
    v_earn  numeric;
    v_earn0 numeric;
BEGIN
    -- Borrow a ride that is NOT already paid, so the paid-gate test is meaningful.
    SELECT r.id, r.rider_id, COALESCE(r.driver_earnings,0)
      INTO v_rid, v_uid, v_earn0
      FROM rides r
     WHERE r.payment_status IS DISTINCT FROM 'paid'
     LIMIT 1;

    IF v_rid IS NULL THEN
        PERFORM pg_temp._skip('288 atomic settle', 'no non-paid ride available to exercise safely');
        RETURN;
    END IF;
    IF v_uid IS NULL OR NOT EXISTS (SELECT 1 FROM users WHERE id = v_uid) THEN
        SELECT id INTO v_uid FROM users LIMIT 1;
    END IF;

    -- 5a. happy path: returns the event id, flips the ride, writes the header
    v_ret := settle_ride_card_payment(
        v_rid, v_ev, v_uid, 2000, 'pi_settle_verify', 2.00,
        '{"source":"process_payment"}'::jsonb, 'captured');

    SELECT payment_status, COALESCE(tip_amount,0), COALESCE(driver_earnings,0)
      INTO v_stat, v_tip, v_earn FROM rides WHERE id = v_rid;

    IF v_ret = v_ev::text AND v_stat = 'paid'
        THEN PERFORM pg_temp._ok('288 settle flips ride to paid and returns event id');
        ELSE PERFORM pg_temp._bad('288 settle flips ride to paid',
             'returned=' || coalesce(v_ret,'NULL') || ' status=' || coalesce(v_stat,'NULL'));
    END IF;

    IF EXISTS (SELECT 1 FROM financial_events WHERE id = v_ev AND delta_cents = 2000)
        THEN PERFORM pg_temp._ok('288 settle writes the ledger header in the same transaction');
        ELSE PERFORM pg_temp._bad('288 settle writes the ledger header', 'header missing — atomicity broken');
    END IF;

    IF v_tip = 2.00
        THEN PERFORM pg_temp._ok('288 tip written');
        ELSE PERFORM pg_temp._bad('288 tip written', 'tip_amount=' || v_tip);
    END IF;

    -- 5b. idempotency: a second call must RETURN NULL and write nothing new
    v_ret := settle_ride_card_payment(
        v_rid, v_ev2, v_uid, 2000, 'pi_settle_verify', 2.00,
        '{"source":"process_payment"}'::jsonb, 'captured');

    IF v_ret IS NULL
        THEN PERFORM pg_temp._ok('288 paid-gate returns NULL on replay');
        ELSE PERFORM pg_temp._bad('288 paid-gate returns NULL on replay',
             'CRITICAL: returned ' || v_ret || ' — a second header may have been written');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM financial_events WHERE id = v_ev2)
        THEN PERFORM pg_temp._ok('288 replay writes NO second header');
        ELSE PERFORM pg_temp._bad('288 replay writes NO second header', 'CRITICAL: duplicate tax-ledger row');
    END IF;

    -- 5c. ON CONFLICT(id): same event id again must not raise
    BEGIN
        PERFORM settle_ride_card_payment(
            v_rid, v_ev, v_uid, 2000, 'pi_settle_verify', 2.00, '{}'::jsonb, 'captured');
        PERFORM pg_temp._ok('288 same-event-id replay does not raise');
    EXCEPTION WHEN OTHERS THEN
        PERFORM pg_temp._bad('288 same-event-id replay does not raise', SQLSTATE || ' ' || SQLERRM);
    END;

    -- 5d. unknown ride must raise P0002, not silently no-op
    BEGIN
        PERFORM settle_ride_card_payment(
            'ride_does_not_exist_verify', gen_random_uuid(), v_uid, 100, 'pi_x', 0, '{}'::jsonb, NULL);
        PERFORM pg_temp._bad('288 unknown ride raises', 'silently accepted a non-existent ride');
    EXCEPTION WHEN OTHERS THEN
        PERFORM pg_temp._ok('288 unknown ride raises', '(' || SQLSTATE || ')');
    END;

    -- 5e. negative amount must be rejected
    BEGIN
        PERFORM settle_ride_card_payment(
            v_rid, gen_random_uuid(), v_uid, -1, 'pi_neg', 0, '{}'::jsonb, NULL);
        PERFORM pg_temp._bad('288 negative amount rejected', 'a negative charge was accepted');
    EXCEPTION WHEN OTHERS THEN
        PERFORM pg_temp._ok('288 negative amount rejected', '(' || SQLSTATE || ')');
    END;
END $$;

-- 5f. Tip delta must move BOTH ways and clamp at 0 — the property that has to
--     match payment_service._tip_ride_update exactly.
DO $$
DECLARE
    v_rid text; v_uid text; v_earn numeric; v_start numeric;
BEGIN
    SELECT r.id, r.rider_id INTO v_rid, v_uid
      FROM rides r WHERE r.payment_status IS DISTINCT FROM 'paid' LIMIT 1;
    IF v_rid IS NULL THEN
        PERFORM pg_temp._skip('288 tip delta direction', 'no non-paid ride available');
        RETURN;
    END IF;
    IF v_uid IS NULL OR NOT EXISTS (SELECT 1 FROM users WHERE id = v_uid) THEN
        SELECT id INTO v_uid FROM users LIMIT 1;
    END IF;

    UPDATE rides SET driver_earnings = 10.00, tip_amount = 5.00, payment_status = 'pending'
     WHERE id = v_rid;

    -- downward correction 5.00 -> 1.00 should REMOVE 4.00 from earnings
    PERFORM settle_ride_card_payment(v_rid, gen_random_uuid(), v_uid, 1000, 'pi_tipdown', 1.00, '{}'::jsonb, NULL);
    SELECT driver_earnings INTO v_earn FROM rides WHERE id = v_rid;
    IF v_earn = 6.00
        THEN PERFORM pg_temp._ok('288 downward tip correction claws back driver_earnings');
        ELSE PERFORM pg_temp._bad('288 downward tip correction', 'expected 6.00, got ' || v_earn);
    END IF;

    -- clamp at zero
    UPDATE rides SET driver_earnings = 1.00, tip_amount = 50.00, payment_status = 'pending'
     WHERE id = v_rid;
    PERFORM settle_ride_card_payment(v_rid, gen_random_uuid(), v_uid, 100, 'pi_tipclamp', 0, '{}'::jsonb, NULL);
    SELECT driver_earnings INTO v_earn FROM rides WHERE id = v_rid;
    IF v_earn >= 0
        THEN PERFORM pg_temp._ok('288 driver_earnings clamps at >= 0', '(got ' || v_earn || ')');
        ELSE PERFORM pg_temp._bad('288 driver_earnings clamps at >= 0', 'went NEGATIVE: ' || v_earn);
    END IF;
END $$;


-- ===========================================================================
-- 6. Summary — raises if anything failed, so psql exits non-zero.
-- ===========================================================================
DO $$
DECLARE p int; f int; s int; r record;
BEGIN
    SELECT count(*) FILTER (WHERE status='PASS'),
           count(*) FILTER (WHERE status='FAIL'),
           count(*) FILTER (WHERE status='SKIP')
      INTO p, f, s FROM _v;

    RAISE NOTICE '';
    RAISE NOTICE '==========================================================';
    RAISE NOTICE ' migrations 286-289 verification: % passed, % failed, % skipped', p, f, s;
    RAISE NOTICE '==========================================================';

    IF f > 0 THEN
        FOR r IN SELECT check_name, detail FROM _v WHERE status='FAIL' LOOP
            RAISE NOTICE '  FAILED: % %', r.check_name, r.detail;
        END LOOP;
    END IF;
    IF s > 0 THEN
        FOR r IN SELECT check_name, detail FROM _v WHERE status='SKIP' LOOP
            RAISE NOTICE '  SKIPPED: % (%)', r.check_name, r.detail;
        END LOOP;
    END IF;

    RAISE NOTICE '';
    RAISE NOTICE 'Everything above is being ROLLED BACK — nothing was committed.';

    IF f > 0 THEN
        RAISE EXCEPTION '% verification check(s) FAILED — see the list above', f;
    END IF;
END $$;

ROLLBACK;
