-- 448_revoke_client_exec_admin_rpcs.sql
--
-- Purpose:
--   Close anon/authenticated EXECUTE on 15 admin_* SECURITY DEFINER RPCs and
--   anon EXECUTE on is_party_to_lost_and_found_case(text). Supabase advisor
--   `anon_security_definer_function_executable` flagged all 16 as callable
--   via /rest/v1/rpc/<name> with the public anon key.
--
-- Root cause (verified read-only against prod soavhtdhefowwvforzwb,
-- 2026-09-23 02:5x UTC, pg_proc.proacl + has_function_privilege):
--   Migrations 380-395 each end with
--       REVOKE EXECUTE ON FUNCTION <fn> FROM anon, authenticated;
--   That is the exact no-op pattern migration 354 documented: it strips the
--   explicit anon/authenticated entries that `pg_default_acl` (postgres, public,
--   functions -> anon, authenticated, service_role) adds, but leaves PUBLIC's
--   default EXECUTE, so anon and authenticated still inherit it. Live ACL on
--   all 15 admin fns:
--       {=X/postgres,postgres=X/postgres,service_role=X/postgres}
--   i.e. PUBLIC=X -> anon=true, authenticated=true, service_role=true.
--   Migration 354's sweep ran on 2026-08-21, BEFORE 380-395 (2026-09-02), so
--   it never saw these functions.
--
--   None of the 15 contain an internal auth guard (no auth.uid()/auth.role()/
--   JWT reference in prosrc -- verified), and SECURITY DEFINER bypasses RLS,
--   so any holder of the public anon key could read MRR, payout windows,
--   promo/dispute/subscription rollups, per-driver ride/bonus summaries,
--   referral boards, audit-log actor stats, and email-log stats.
--
-- Callers (git grep over backend/, admin-dashboard/, rider-app/, driver-app/,
-- shared/ at d807392c6):
--   * Every admin_* function is called ONLY from backend/routes/admin/*.py via
--     db_supabase.rpc(...), which uses supabase_client.py's client built from
--     SUPABASE_SERVICE_ROLE_KEY -> PostgREST role service_role. service_role
--     keeps EXECUTE (explicitly re-granted below).
--   * admin-dashboard has no supabase-js dependency and no .rpc( call; it only
--     calls the FastAPI backend. rider-app/driver-app depend on supabase-js but
--     reference none of these function names.
--   * is_party_to_lost_and_found_case is not called by any application code.
--     It is referenced by RLS policies lfm_select / lfm_insert on
--     public.lost_and_found_messages (migration 412; pg_policies confirms both,
--     roles {public}). Postgres checks EXECUTE on functions in a policy
--     expression as the INVOKING role, so authenticated must keep EXECUTE or
--     a rider/driver JWT querying lost_and_found_messages directly would error.
--     anon has no legitimate use: the function compares auth.uid() (NULL for
--     anon) to reporter_id/driver_id, so it can only ever return false for
--     anon. After this migration an anon query of lost_and_found_messages gets
--     42501 permission denied instead of zero rows -- deny either way.
--
-- Safety:
--   * Privileges only. No function body, table, policy, or row is changed.
--   * service_role is GRANTed in the same statement batch as the REVOKE, so
--     the backend is never left without EXECUTE.
--   * Idempotent: REVOKE/GRANT of an absent/present privilege is a no-op.
--   * Fails loudly if any listed signature is missing (schema drift) and
--     post-checks the resulting privileges.
--   * ALTER DEFAULT PRIVILEGES is deliberately NOT changed here (see PR): the
--     postgres/public default ACL grants EXECUTE to anon/authenticated on every
--     new function. Removing it is the real root-cause fix but would silently
--     break any future RLS helper that forgets an explicit grant; it needs its
--     own reviewed change. backend/tests/test_admin_secdef_fn_revokes.py is the
--     guard until then.
--
-- Rollback (restores the INSECURE state -- only if something unexpected
-- depended on client EXECUTE; the right fix then is a targeted grant):
--   DO $$ DECLARE s text; BEGIN
--     FOREACH s IN ARRAY ARRAY[
--       'public.admin_audit_actor_stats(timestamptz,integer)',
--       'public.admin_cloud_message_stats_rollup()',
--       'public.admin_daily_ride_stats(timestamptz,timestamptz,text[])',
--       'public.admin_dispute_stats_rollup()',
--       'public.admin_driver_bonus_summary(text,timestamptz)',
--       'public.admin_driver_referral_board(integer)',
--       'public.admin_driver_ride_summary(text,timestamptz,timestamptz)',
--       'public.admin_email_log_stats(timestamptz)',
--       'public.admin_incentive_claims_sum(timestamptz,timestamptz)',
--       'public.admin_mrr_at_cutoff(timestamptz)',
--       'public.admin_payout_period_snapshot(timestamptz,timestamptz)',
--       'public.admin_payout_window_stats(timestamptz,timestamptz,timestamptz,timestamptz,text)',
--       'public.admin_promo_stats(timestamptz,timestamptz)',
--       'public.admin_referred_user_count(text,text,text)',
--       'public.admin_subscription_stats_rollup(timestamptz,timestamptz,text[])']
--     LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO PUBLIC', s); END LOOP;
--   END $$;
--   GRANT EXECUTE ON FUNCTION public.is_party_to_lost_and_found_case(text) TO anon;
--   NOTIFY pgrst, 'reload schema';

-- Fail loudly on drift rather than half-applying.
DO $$
DECLARE
    s text;
BEGIN
    FOREACH s IN ARRAY ARRAY[
        'public.admin_audit_actor_stats(timestamptz,integer)',
        'public.admin_cloud_message_stats_rollup()',
        'public.admin_daily_ride_stats(timestamptz,timestamptz,text[])',
        'public.admin_dispute_stats_rollup()',
        'public.admin_driver_bonus_summary(text,timestamptz)',
        'public.admin_driver_referral_board(integer)',
        'public.admin_driver_ride_summary(text,timestamptz,timestamptz)',
        'public.admin_email_log_stats(timestamptz)',
        'public.admin_incentive_claims_sum(timestamptz,timestamptz)',
        'public.admin_mrr_at_cutoff(timestamptz)',
        'public.admin_payout_period_snapshot(timestamptz,timestamptz)',
        'public.admin_payout_window_stats(timestamptz,timestamptz,timestamptz,timestamptz,text)',
        'public.admin_promo_stats(timestamptz,timestamptz)',
        'public.admin_referred_user_count(text,text,text)',
        'public.admin_subscription_stats_rollup(timestamptz,timestamptz,text[])',
        'public.is_party_to_lost_and_found_case(text)'
    ]
    LOOP
        IF to_regprocedure(s) IS NULL THEN
            RAISE EXCEPTION 'migration 448: expected function % not found (schema drift)', s;
        END IF;
    END LOOP;
END $$;

-- 15 admin_* read aggregates: backend (service_role) only.
REVOKE EXECUTE ON FUNCTION public.admin_audit_actor_stats(timestamptz, integer) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_audit_actor_stats(timestamptz, integer) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_cloud_message_stats_rollup() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_cloud_message_stats_rollup() TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_daily_ride_stats(timestamptz, timestamptz, text[]) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_daily_ride_stats(timestamptz, timestamptz, text[]) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_dispute_stats_rollup() FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_dispute_stats_rollup() TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_driver_bonus_summary(text, timestamptz) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_driver_bonus_summary(text, timestamptz) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_driver_referral_board(integer) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_driver_referral_board(integer) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_driver_ride_summary(text, timestamptz, timestamptz) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_driver_ride_summary(text, timestamptz, timestamptz) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_email_log_stats(timestamptz) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_email_log_stats(timestamptz) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_incentive_claims_sum(timestamptz, timestamptz) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_incentive_claims_sum(timestamptz, timestamptz) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_mrr_at_cutoff(timestamptz) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_mrr_at_cutoff(timestamptz) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_payout_period_snapshot(timestamptz, timestamptz) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_payout_period_snapshot(timestamptz, timestamptz) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_payout_window_stats(timestamptz, timestamptz, timestamptz, timestamptz, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_payout_window_stats(timestamptz, timestamptz, timestamptz, timestamptz, text) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_promo_stats(timestamptz, timestamptz) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_promo_stats(timestamptz, timestamptz) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_referred_user_count(text, text, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_referred_user_count(text, text, text) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_subscription_stats_rollup(timestamptz, timestamptz, text[]) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_subscription_stats_rollup(timestamptz, timestamptz, text[]) TO service_role;

-- RLS helper (migration 412): used by lfm_select/lfm_insert policies, so
-- authenticated MUST keep EXECUTE. anon loses it (always false for anon).
REVOKE EXECUTE ON FUNCTION public.is_party_to_lost_and_found_case(text) FROM PUBLIC, anon;
GRANT  EXECUTE ON FUNCTION public.is_party_to_lost_and_found_case(text) TO authenticated, service_role;

-- Post-condition: fail the migration rather than report a half-applied lockdown.
DO $$
DECLARE
    leaked text;
BEGIN
    SELECT string_agg(p.oid::regprocedure::text, ', ') INTO leaked
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname IN (
          'admin_audit_actor_stats', 'admin_cloud_message_stats_rollup',
          'admin_daily_ride_stats', 'admin_dispute_stats_rollup',
          'admin_driver_bonus_summary', 'admin_driver_referral_board',
          'admin_driver_ride_summary', 'admin_email_log_stats',
          'admin_incentive_claims_sum', 'admin_mrr_at_cutoff',
          'admin_payout_period_snapshot', 'admin_payout_window_stats',
          'admin_promo_stats', 'admin_referred_user_count',
          'admin_subscription_stats_rollup')
      AND (has_function_privilege('anon',          p.oid, 'EXECUTE')
        OR has_function_privilege('authenticated', p.oid, 'EXECUTE')
        OR NOT has_function_privilege('service_role', p.oid, 'EXECUTE'));
    IF leaked IS NOT NULL THEN
        RAISE EXCEPTION 'migration 448 post-condition failed for: %', leaked;
    END IF;

    IF has_function_privilege('anon', 'public.is_party_to_lost_and_found_case(text)', 'EXECUTE')
       OR NOT has_function_privilege('authenticated', 'public.is_party_to_lost_and_found_case(text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'migration 448 post-condition failed for is_party_to_lost_and_found_case(text)';
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
