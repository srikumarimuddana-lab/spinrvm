-- 474_function_search_path_hardening.sql
--
-- Purpose (docs/audit/clean-sheet/10-live-checks.md §3, LIVE-002):
--   Pin search_path on the 16 public functions the Supabase security linter
--   (`function_search_path_mutable`, lint 0011) reports as having a
--   role-mutable search_path. Without a pinned path, each unqualified name in
--   a body (tables, types, PostGIS functions and operators) resolves against
--   the CALLER's search_path at call time. A role that can create objects
--   earlier on its own path, or in pg_temp, could then shadow them.
--
-- What this migration does:
--   * One `ALTER FUNCTION ... SET search_path = ...` per function. No function
--     BODY, signature, owner, volatility, SECURITY mode, or grant changes.
--   * Fails loudly on schema drift: every signature is checked before the
--     ALTERs, and a post-condition checks that each function now has a pinned
--     search_path.
--
-- How each path was chosen (verified with read-only catalog queries against
-- prod soavhtdhefowwvforzwb on 2026-09-25: pg_proc.prosrc,
-- pg_get_function_identity_arguments, and pg_extension joined to
-- pg_namespace):
--   * PostGIS 3.3.7 is installed in schema `extensions`, not in `public`.
--     These 4 bodies call ST_SetSRID/ST_MakePoint/ST_DWithin/ST_Distance/
--     ST_X/ST_Y/ST_Intersects, cast to `geography`/`geometry`, or use the
--     geography `<->` operator, all UNQUALIFIED. They get
--     `public, extensions, pg_temp`:
--         find_nearby_drivers, get_service_area_for_point,
--         match_and_claim_driver, update_driver_location
--     Live callers: update_driver_location (driver location writes) and
--     get_service_area_for_point. match_and_claim_driver and
--     find_nearby_drivers have no production caller (superseded per
--     migrations 402/403/448 and routes/rides/matching.py); pinned anyway.
--     If `extensions` were left out, these calls would fail with "function
--     st_makepoint(...) does not exist" and dispatch would break.
--   * The other 12 reference only `public` tables (wallets,
--     fare_split_participants, promotions) or built-ins that are always
--     reachable through the implicit pg_catalog (now(), current_setting,
--     RAISE, TG_* variables). They get `public, pg_temp`, the repo's usual
--     pattern (see migrations 434, 436, 445 and 447).
--   * pg_temp is listed LAST on purpose, so a temp-schema object can never
--     shadow a real one. pg_catalog is omitted because Postgres always
--     searches it first when it is not listed.
--   * No body uses pgcrypto, uuid-ossp, http, pgsodium, vault, topology or
--     tiger objects.
--
-- Why the path is not already effectively fixed today:
--   The live database default is `"$user", public, extensions, topology,
--   tiger` (pg_db_role_setting, setrole = 0). That is why these unqualified
--   PostGIS calls work now. This migration keeps the same resolution for
--   every name these bodies use (public, then extensions); it only stops it
--   from depending on the caller.
--
-- Signatures use argument TYPES only, so drift in parameter names does not
-- matter. Example: backend/supabase_schema.sql names update_driver_location's
-- first argument `p_driver_id`, but in prod it is `driver_id`. Both are the
-- identity (text, double precision, double precision).
--
-- Side effect worth knowing:
--   A LANGUAGE sql function with a SET clause is no longer inlined by the
--   planner. find_nearby_drivers and get_service_area_for_point (both STABLE
--   sql) are therefore now run as a function scan, not inlined into the
--   calling query. The body's own query plan (GiST index on location/area)
--   does not change. PostgREST calls them as `SELECT ... FROM fn(...)` with no
--   outer predicate to push down, so the expected latency change is
--   negligible. This was not measured.
--
-- Out of scope: LIVE-003 (revoking authenticated EXECUTE on
-- is_party_to_lost_and_found_case(text)) is deliberately NOT done here.
--   Migration 450 kept that grant on purpose. RLS policies lfm_select and
--   lfm_insert on public.lost_and_found_messages (roles {public}) call the
--   function, and Postgres checks EXECUTE as the invoking role. Revoking it
--   would turn those policies into hard 42501 errors for `authenticated`.
--   backend/tests/test_admin_secdef_fn_revokes.py
--   (test_450_keeps_authenticated_on_rls_helper_but_drops_anon) pins that
--   decision. The function already has a pinned search_path
--   (public, pg_catalog). See docs/change-log/2026-09-25-function-search-path-hardening.md.
--
-- Safety:
--   * Metadata only (pg_proc.proconfig). No table, row, policy or grant is
--     touched. No table lock is taken.
--   * Idempotent: re-running sets the same value.
--   * Calls already running finish with the old setting. The next call picks
--     up the pinned path (the plpgsql function cache is invalidated when the
--     pg_proc row changes).
--
-- Rollback (restores the linter-flagged, caller-dependent search_path; safe
-- at any time, metadata only):
--   ALTER FUNCTION public.match_and_claim_driver(text, double precision, double precision, double precision, double precision) RESET search_path;
--   ALTER FUNCTION public.find_nearby_drivers(double precision, double precision, double precision) RESET search_path;
--   ALTER FUNCTION public.get_service_area_for_point(double precision, double precision) RESET search_path;
--   ALTER FUNCTION public.update_driver_location(text, double precision, double precision) RESET search_path;
--   ALTER FUNCTION public.fare_split_pay_share(uuid, uuid, numeric) RESET search_path;
--   ALTER FUNCTION public.increment_promo_uses(uuid, integer) RESET search_path;
--   ALTER FUNCTION public._audit_logs_immutable() RESET search_path;
--   ALTER FUNCTION public.audit_logs_block_delete() RESET search_path;
--   ALTER FUNCTION public.audit_logs_block_update() RESET search_path;
--   ALTER FUNCTION public._financial_events_immutable() RESET search_path;
--   ALTER FUNCTION public._financial_event_entries_immutable() RESET search_path;
--   ALTER FUNCTION public._subscription_payments_immutable() RESET search_path;
--   ALTER FUNCTION public.block_mutation_on_immutable_table() RESET search_path;
--   ALTER FUNCTION public.disputes_block_delete() RESET search_path;
--   ALTER FUNCTION public.safety_incidents_set_updated_at() RESET search_path;
--   ALTER FUNCTION public.update_updated_at_column() RESET search_path;

BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

-- Fail loudly on drift rather than half-applying.
DO $$
DECLARE
    s text;
BEGIN
    FOREACH s IN ARRAY ARRAY[
        'public.match_and_claim_driver(text, double precision, double precision, double precision, double precision)',
        'public.find_nearby_drivers(double precision, double precision, double precision)',
        'public.get_service_area_for_point(double precision, double precision)',
        'public.update_driver_location(text, double precision, double precision)',
        'public.fare_split_pay_share(uuid, uuid, numeric)',
        'public.increment_promo_uses(uuid, integer)',
        'public._audit_logs_immutable()',
        'public.audit_logs_block_delete()',
        'public.audit_logs_block_update()',
        'public._financial_events_immutable()',
        'public._financial_event_entries_immutable()',
        'public._subscription_payments_immutable()',
        'public.block_mutation_on_immutable_table()',
        'public.disputes_block_delete()',
        'public.safety_incidents_set_updated_at()',
        'public.update_updated_at_column()'
    ]
    LOOP
        IF to_regprocedure(s) IS NULL THEN
            RAISE EXCEPTION 'migration 474: expected function % not found (schema drift)', s;
        END IF;
    END LOOP;
END $$;

-- ── PostGIS callers: unqualified ST_* / geography live in `extensions` ──────
-- match_and_claim_driver / find_nearby_drivers: no production caller today.
-- get_service_area_for_point / update_driver_location: live callers.
ALTER FUNCTION public.match_and_claim_driver(text, double precision, double precision, double precision, double precision)
    SET search_path = public, extensions, pg_temp;
ALTER FUNCTION public.find_nearby_drivers(double precision, double precision, double precision)
    SET search_path = public, extensions, pg_temp;
ALTER FUNCTION public.get_service_area_for_point(double precision, double precision)
    SET search_path = public, extensions, pg_temp;
ALTER FUNCTION public.update_driver_location(text, double precision, double precision)
    SET search_path = public, extensions, pg_temp;

-- ── Money RPCs: public tables only ──────────────────────────────────────────
ALTER FUNCTION public.fare_split_pay_share(uuid, uuid, numeric)
    SET search_path = public, pg_temp;
ALTER FUNCTION public.increment_promo_uses(uuid, integer)
    SET search_path = public, pg_temp;

-- ── Append-only / immutability trigger guards: built-ins only ───────────────
ALTER FUNCTION public._audit_logs_immutable()               SET search_path = public, pg_temp;
ALTER FUNCTION public.audit_logs_block_delete()             SET search_path = public, pg_temp;
ALTER FUNCTION public.audit_logs_block_update()             SET search_path = public, pg_temp;
ALTER FUNCTION public._financial_events_immutable()         SET search_path = public, pg_temp;
ALTER FUNCTION public._financial_event_entries_immutable()  SET search_path = public, pg_temp;
ALTER FUNCTION public._subscription_payments_immutable()    SET search_path = public, pg_temp;
ALTER FUNCTION public.block_mutation_on_immutable_table()   SET search_path = public, pg_temp;
ALTER FUNCTION public.disputes_block_delete()               SET search_path = public, pg_temp;

-- ── updated_at trigger helpers: built-ins only ──────────────────────────────
ALTER FUNCTION public.safety_incidents_set_updated_at()     SET search_path = public, pg_temp;
ALTER FUNCTION public.update_updated_at_column()            SET search_path = public, pg_temp;

-- Post-condition: every target now carries a pinned search_path, and the
-- PostGIS callers' path includes `extensions`.
DO $$
DECLARE
    missing text;
    no_ext  text;
BEGIN
    SELECT string_agg(p.oid::regprocedure::text, ', ') INTO missing
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname IN (
          'match_and_claim_driver', 'find_nearby_drivers',
          'get_service_area_for_point', 'update_driver_location',
          'fare_split_pay_share', 'increment_promo_uses',
          '_audit_logs_immutable', 'audit_logs_block_delete',
          'audit_logs_block_update', '_financial_events_immutable',
          '_financial_event_entries_immutable', '_subscription_payments_immutable',
          'block_mutation_on_immutable_table', 'disputes_block_delete',
          'safety_incidents_set_updated_at', 'update_updated_at_column')
      AND NOT EXISTS (
          SELECT 1 FROM unnest(coalesce(p.proconfig, '{}'::text[])) c
          WHERE c LIKE 'search_path=%');
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'migration 474 post-condition failed, search_path not pinned on: %', missing;
    END IF;

    SELECT string_agg(p.oid::regprocedure::text, ', ') INTO no_ext
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname IN (
          'match_and_claim_driver', 'find_nearby_drivers',
          'get_service_area_for_point', 'update_driver_location')
      AND NOT EXISTS (
          SELECT 1 FROM unnest(coalesce(p.proconfig, '{}'::text[])) c
          WHERE c LIKE 'search_path=%extensions%');
    IF no_ext IS NOT NULL THEN
        RAISE EXCEPTION 'migration 474 post-condition failed, PostGIS caller missing extensions on path: %', no_ext;
    END IF;
END $$;

COMMIT;
