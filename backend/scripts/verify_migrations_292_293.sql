-- verify_migrations_292_293.sql
--
-- Runtime verification for migrations 292-293 (date-scoped trial-balance check
-- for the double-entry legs).
--
-- Companion to verify_migrations_286_291.sql, kept SEPARATE on purpose: that
-- script is a recorded artifact whose "all checks passed" run on 2026-08-08 is
-- cited in docs/change-log/2026-08-08-migration-verification-result.md. Editing
-- it would break the provenance of that result. Run both.
--
-- WHY THIS EXISTS
--   Both migrations were authored with no reachable Postgres and validated
--   with pglast, which proves SYNTAX ONLY. It does not prove that
--   RETURNS SETOF financial_event_entries_unbalanced actually matches the
--   view's column types (SUM(bigint) yields numeric, not bigint — get that
--   wrong and CREATE FUNCTION fails), that the window bounds are half-open,
--   that the function agrees with the view, or that the grants landed.
--
-- ============================================================================
-- SAFETY
--   * Run on STAGING (or any throwaway copy). Do NOT run against production.
--   * The whole script runs inside ONE transaction and ends in ROLLBACK, so it
--     leaves nothing behind even on success. Nothing is committed. Ever.
--   * It borrows one existing user and one existing ride, read-only apart from
--     the rolled-back writes, exactly as the 286-291 script does.
--
-- PREREQUISITE
--   Migrations 286-293 applied:
--       cd backend && python scripts/migrate.py          # or --dry-run first
--   NOTE 292 creates an index CONCURRENTLY, so migrate.py runs that file
--   outside a transaction. If it is interrupted, Postgres can leave an INVALID
--   index behind; check with
--       SELECT indexrelid::regclass, indisvalid FROM pg_index
--        WHERE indexrelid = 'financial_event_entries_created_at'::regclass;
--   and DROP INDEX CONCURRENTLY + re-run if indisvalid is false.
--
-- RUN
--       psql "$PG_CONNECTION_STRING" -v ON_ERROR_STOP=1 \
--            -f backend/scripts/verify_migrations_292_293.sql
--
-- READ THE OUTPUT
--   Every check prints "PASS: ..." or "FAIL: ...". The final block raises if
--   anything failed, so a non-zero psql exit == failure. A "SKIP: ..." is NOT
--   a pass — please report it back.
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
    RAISE WARNING 'SKIP: % (%)', p_name, p_detail;
END; $$;


-- ============================================================================
-- 1. Objects exist
-- ============================================================================
DO $$
DECLARE v_valid boolean;
BEGIN
    IF to_regclass('public.financial_event_entries_created_at') IS NOT NULL
        THEN PERFORM pg_temp._ok('292 index exists');
        ELSE PERFORM pg_temp._bad('292 index exists', 'financial_event_entries_created_at missing');
    END IF;

    -- CONCURRENTLY can leave an INVALID index behind if interrupted. An
    -- invalid index is not used by the planner, so the fix would look applied
    -- while the nightly check kept sequential-scanning.
    SELECT indisvalid INTO v_valid
      FROM pg_index
     WHERE indexrelid = to_regclass('public.financial_event_entries_created_at');
    IF v_valid IS NULL
        THEN PERFORM pg_temp._skip('292 index is VALID', 'index not present');
    ELSIF v_valid
        THEN PERFORM pg_temp._ok('292 index is VALID');
        ELSE PERFORM pg_temp._bad('292 index is VALID',
                 'indisvalid=false — interrupted CONCURRENTLY build, planner will ignore it');
    END IF;

    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'financial_event_entries_unbalanced_between')
        THEN PERFORM pg_temp._ok('293 function exists');
        ELSE PERFORM pg_temp._bad('293 function exists', 'financial_event_entries_unbalanced_between missing');
    END IF;
END $$;


-- ============================================================================
-- 2. Grants — service_role only, same posture as the view (migration 286)
-- ============================================================================
DO $$
DECLARE v_sig text := 'financial_event_entries_unbalanced_between(timestamptz, timestamptz)';
BEGIN
    IF has_function_privilege('anon', v_sig, 'EXECUTE')
        OR has_function_privilege('authenticated', v_sig, 'EXECUTE')
        THEN PERFORM pg_temp._bad('293 EXECUTE revoked from JWT roles',
                 'a client role can call the SECURITY DEFINER trial-balance function');
        ELSE PERFORM pg_temp._ok('293 EXECUTE revoked from JWT roles');
    END IF;

    -- REVOKE ... FROM PUBLIC also strips service_role's inherited EXECUTE,
    -- which is why migration 205's form grants it back explicitly. If this
    -- fails, the nightly check is dead in production and silent about it.
    IF has_function_privilege('service_role', v_sig, 'EXECUTE')
        THEN PERFORM pg_temp._ok('293 service_role retains EXECUTE');
        ELSE PERFORM pg_temp._bad('293 service_role retains EXECUTE',
                 'REVOKE FROM PUBLIC stripped it and the GRANT did not restore it');
    END IF;
END $$;


-- ============================================================================
-- 3. Behaviour — the function must agree with the view, and must be scoped
-- ============================================================================
DO $$
DECLARE
    v_uid   text;
    v_rid   text;
    v_bal   uuid := gen_random_uuid();   -- balanced, inside the window
    v_bad   uuid := gen_random_uuid();   -- lopsided, inside the window
    v_old   uuid := gen_random_uuid();   -- lopsided, OUTSIDE the window
    v_t0    timestamptz := date_trunc('day', now());
    v_t1    timestamptz := date_trunc('day', now()) + interval '1 day';
    v_n     int;
    v_view  int;
BEGIN
    SELECT id INTO v_uid FROM users LIMIT 1;
    SELECT id INTO v_rid FROM rides LIMIT 1;
    IF v_uid IS NULL OR v_rid IS NULL THEN
        PERFORM pg_temp._skip('293 behaviour', 'no users/rides rows to borrow');
        RETURN;
    END IF;

    -- A balanced journal inside today's window.
    INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at)
    VALUES (v_bal, 'stripe_charge', v_uid, v_rid, 2100, 'pi_v292_bal', '{}'::jsonb, now());
    INSERT INTO financial_event_entries (event_id, account, side, amount_cents, created_at) VALUES
        (v_bal, 'stripe_receivable', 'debit',  1600, v_t0 + interval '3 hours'),
        (v_bal, 'promo_expense',     'debit',   500, v_t0 + interval '3 hours'),
        (v_bal, 'driver_payable',    'credit', 1750, v_t0 + interval '3 hours'),
        (v_bal, 'tax_payable',       'credit',  100, v_t0 + interval '3 hours'),
        (v_bal, 'platform_revenue',  'credit',  250, v_t0 + interval '3 hours');

    -- 3a. A balanced journal must NOT be reported. This also proves the
    --     promo_expense account is accepted by 286's CHECK constraint — the
    --     first code path to emit it is the promo-legs fix.
    SELECT count(*) INTO v_n
      FROM financial_event_entries_unbalanced_between(v_t0, v_t1) WHERE event_id = v_bal;
    IF v_n = 0
        THEN PERFORM pg_temp._ok('293 ignores a balanced journal (promo legs accepted)');
        ELSE PERFORM pg_temp._bad('293 ignores a balanced journal', 'reported a balanced entry');
    END IF;

    -- A lopsided journal inside today's window.
    INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at)
    VALUES (v_bad, 'stripe_charge', v_uid, v_rid, 500, 'pi_v292_bad', '{}'::jsonb, now());
    INSERT INTO financial_event_entries (event_id, account, side, amount_cents, created_at)
    VALUES (v_bad, 'stripe_receivable', 'debit', 500, v_t0 + interval '4 hours');  -- no counter-leg

    -- 3b. ...must be caught.
    SELECT count(*) INTO v_n
      FROM financial_event_entries_unbalanced_between(v_t0, v_t1) WHERE event_id = v_bad;
    IF v_n = 1
        THEN PERFORM pg_temp._ok('293 catches a lopsided journal');
        ELSE PERFORM pg_temp._bad('293 catches a lopsided journal', 'expected 1 row, got ' || v_n);
    END IF;

    -- 3c. ...and must report the SAME shape the view does. If the scoped and
    --     unscoped paths ever disagree, an operator reading the view by hand
    --     and the nightly job would tell different stories.
    SELECT count(*) INTO v_view FROM financial_event_entries_unbalanced WHERE event_id = v_bad;
    IF v_view = v_n
        THEN PERFORM pg_temp._ok('293 agrees with the unscoped view');
        ELSE PERFORM pg_temp._bad('293 agrees with the unscoped view',
                 'view says ' || v_view || ', function says ' || v_n);
    END IF;

    IF EXISTS (
        SELECT 1 FROM financial_event_entries_unbalanced_between(v_t0, v_t1) f
         WHERE f.event_id = v_bad
           AND f.debit_cents = 500 AND f.credit_cents = 0 AND f.imbalance_cents = 500
    )
        THEN PERFORM pg_temp._ok('293 column values are correct');
        ELSE PERFORM pg_temp._bad('293 column values are correct',
                 'debit/credit/imbalance did not match the inserted legs');
    END IF;

    -- A lopsided journal OUTSIDE the window (legs dated yesterday).
    INSERT INTO financial_events (id, event_type, user_id, ride_id, delta_cents, ref, metadata, created_at)
    VALUES (v_old, 'stripe_charge', v_uid, v_rid, 700, 'pi_v292_old', '{}'::jsonb, now() - interval '2 days');
    INSERT INTO financial_event_entries (event_id, account, side, amount_cents, created_at)
    VALUES (v_old, 'stripe_receivable', 'debit', 700, v_t0 - interval '5 hours');

    -- 3d. Scoping actually scopes. If this fails the function is aggregating
    --     more than the window and the whole point of 292/293 is lost.
    SELECT count(*) INTO v_n
      FROM financial_event_entries_unbalanced_between(v_t0, v_t1) WHERE event_id = v_old;
    IF v_n = 0
        THEN PERFORM pg_temp._ok('293 excludes legs before the window');
        ELSE PERFORM pg_temp._bad('293 excludes legs before the window', 'out-of-window event was reported');
    END IF;

    -- 3e. ...but the same event IS found when the window covers it — proving
    --     3d was scoping, not the row being invisible for some other reason.
    SELECT count(*) INTO v_n
      FROM financial_event_entries_unbalanced_between(v_t0 - interval '1 day', v_t1) WHERE event_id = v_old;
    IF v_n = 1
        THEN PERFORM pg_temp._ok('293 finds it once the window covers it');
        ELSE PERFORM pg_temp._bad('293 finds it once the window covers it', 'expected 1 row, got ' || v_n);
    END IF;

    -- 3f. Half-open bounds. v_old's leg sits exactly at (v_t0 - 5h).
    --     A leg landing exactly on p_end must belong to the NEXT window, or
    --     consecutive daily runs double-report the same journal.
    SELECT count(*) INTO v_n
      FROM financial_event_entries_unbalanced_between(v_t0 - interval '6 hours', v_t0 - interval '5 hours')
     WHERE event_id = v_old;
    IF v_n = 0
        THEN PERFORM pg_temp._ok('293 p_end is exclusive');
        ELSE PERFORM pg_temp._bad('293 p_end is exclusive', 'a leg exactly at p_end was included');
    END IF;

    -- 3g. ...and p_start must be INCLUSIVE, or that same journal falls into
    --     the gap between two consecutive windows and is never checked at all.
    SELECT count(*) INTO v_n
      FROM financial_event_entries_unbalanced_between(v_t0 - interval '5 hours', v_t0 - interval '4 hours')
     WHERE event_id = v_old;
    IF v_n = 1
        THEN PERFORM pg_temp._ok('293 p_start is inclusive');
        ELSE PERFORM pg_temp._bad('293 p_start is inclusive',
                 'a leg exactly at p_start was dropped — consecutive windows would miss it');
    END IF;
END $$;


-- ============================================================================
-- 4. Advisory: does the planner actually use the new index?
--    Informational only — on a small/empty staging table Postgres will
--    correctly prefer a seq scan, so this is NOT scored.
-- ============================================================================
DO $$
DECLARE v_plan text;
BEGIN
    EXECUTE $q$
        EXPLAIN (FORMAT TEXT)
        SELECT event_id FROM financial_event_entries
         WHERE created_at >= date_trunc('day', now())
           AND created_at <  date_trunc('day', now()) + interval '1 day'
    $q$ INTO v_plan;
    RAISE NOTICE '';
    RAISE NOTICE 'ADVISORY — window-lookup plan (a seq scan is EXPECTED on a small/empty table): %', v_plan;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'INFO: could not EXPLAIN the window lookup (%).', SQLERRM;
END $$;


-- ============================================================================
-- Summary
-- ============================================================================
DO $$
DECLARE p int; f int; s int; r record;
BEGIN
    SELECT count(*) FILTER (WHERE status='PASS'),
           count(*) FILTER (WHERE status='FAIL'),
           count(*) FILTER (WHERE status='SKIP')
      INTO p, f, s FROM _v;

    RAISE NOTICE '';
    RAISE NOTICE '==========================================================';
    RAISE NOTICE ' migrations 292-293 verification: % passed, % failed, % skipped', p, f, s;
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
