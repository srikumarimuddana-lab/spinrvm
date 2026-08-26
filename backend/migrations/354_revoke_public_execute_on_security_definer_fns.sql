-- 354_revoke_public_execute_on_security_definer_fns.sql
--
-- Purpose:
--   Codify a production hotfix so it survives environment rebuilds.
--
--   Postgres grants EXECUTE to PUBLIC by default on CREATE FUNCTION. Many of
--   this repo's migrations end with
--
--       REVOKE EXECUTE ON FUNCTION <fn> FROM anon, authenticated;
--
--   which is a NO-OP: it removes direct grants those roles never held, while
--   both keep EXECUTE through PUBLIC. On 2026-08-20 an audit of the live
--   database found 18 SECURITY DEFINER functions in `public` executable by
--   `anon` — the role Supabase's publicly-distributed anon key authenticates
--   as, which also holds USAGE on `public`, so PostgREST can route
--   /rest/v1/rpc/<name> to them.
--
--   SECURITY DEFINER runs as the owner and bypasses RLS, and none of the 18
--   carried an internal auth guard (no auth.uid(), auth.role(), or JWT
--   check) — the grant model was the only control. Ten were read-only
--   money/ops aggregates; the rest MUTATE state:
--
--     wallet_pay_for_ride(uuid, uuid, numeric, numeric)   -- moves money
--     corporate_section_spend_add(uuid, text, numeric)    -- spend delta
--     promo_increment_uses(uuid)                          -- promo abuse
--     claim_promo_user_slot(uuid, text, integer)          -- promo abuse
--     release_promo_user_slot(uuid, text)                 -- promo abuse
--     record_insurance_period_transition(text, smallint, text)
--                                     -- writes the regulatory audit table
--     compute_driver_phase_distances(uuid, timestamptz, timestamptz)
--
--   The grants were corrected directly against production the same day, so
--   the live database is already clean. THIS MIGRATION DOES NOT CHANGE
--   PRODUCTION — it is expected to be a no-op there. It exists because the
--   hotfix was applied outside the migration runner and therefore is NOT
--   reproducible: a staging refresh, a DR restore, or a Supabase branch
--   database rebuilt from migrations would come back vulnerable. This file
--   makes the corrected state part of the schema's definition.
--
-- Why a sweep rather than a list of names:
--   Listing the 18 signatures would fix only the functions known on this
--   date. The defect is a PATTERN — every future migration that copies the
--   `FROM anon, authenticated` form reintroduces it. The sweep corrects
--   whatever is wrong at apply time, and RAISE NOTICEs each function it
--   touches so the runner's output is an audit record rather than a silent
--   bulk change.
--
-- Safety:
--   * service_role is GRANTed inside the same loop iteration as the REVOKE,
--     so no function can be left unreachable by the backend. Verified
--     against production before writing this: of 45 SECURITY DEFINER
--     functions in `public`, 0 lacked a service_role grant.
--   * Trigger functions are unaffected in practice — Postgres does not
--     re-check EXECUTE when a trigger fires, so revoking direct-invocation
--     rights does not disable any trigger.
--   * Idempotent. Re-running finds nothing to change and emits no notices.
--   * Read/write of privileges only. No table, column, index, policy, or
--     function BODY is altered. No row is written or migrated.
--
-- If a future function legitimately needs anon or authenticated EXECUTE,
-- grant it explicitly in that function's own migration AFTER this one, and
-- say why in a comment. Do not weaken this sweep.
--
-- Rollback (restores the insecure state — only appropriate if something
-- unexpected turns out to depend on the PUBLIC grant, in which case the
-- right fix is granting that specific role, not PUBLIC):
--   DO $$ DECLARE f record; BEGIN
--     FOR f IN SELECT p.oid::regprocedure AS sig
--              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--              WHERE n.nspname = 'public' AND p.prosecdef
--     LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO PUBLIC', f.sig); END LOOP;
--   END $$;

DO $$
DECLARE
    f       record;
    touched int := 0;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure AS sig
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.prosecdef                                   -- SECURITY DEFINER only
          AND (
                has_function_privilege('anon',          p.oid, 'EXECUTE')
             OR has_function_privilege('authenticated', p.oid, 'EXECUTE')
          )
        ORDER BY 1
    LOOP
        EXECUTE format(
            'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon, authenticated', f.sig);
        -- Granted in the same iteration: a REVOKE without this would strand
        -- the backend, which reaches these only as service_role.
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO service_role', f.sig);
        touched := touched + 1;
        RAISE NOTICE 'migration 354: locked down %', f.sig;
    END LOOP;

    IF touched = 0 THEN
        RAISE NOTICE 'migration 354: no-op — no SECURITY DEFINER function in public was anon/authenticated-executable';
    ELSE
        RAISE NOTICE 'migration 354: locked down % function(s)', touched;
    END IF;
END $$;

-- Post-condition. If the sweep somehow left anything reachable, fail the
-- migration loudly rather than reporting success on a half-applied lockdown.
DO $$
DECLARE leaked int;
BEGIN
    SELECT count(*) INTO leaked
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.prosecdef
      AND (has_function_privilege('anon',          p.oid, 'EXECUTE')
        OR has_function_privilege('authenticated', p.oid, 'EXECUTE'));

    IF leaked > 0 THEN
        RAISE EXCEPTION
          'migration 354 post-condition failed: % SECURITY DEFINER function(s) still anon/authenticated-executable',
          leaked;
    END IF;
END $$;
