-- Migration 412: fix lost_and_found_messages RLS driver-visibility bug
--
-- migration-override-ok: intentional CREATE OR REPLACE / DROP+CREATE POLICY
-- amendment of policies shipped in migration 115 (lost_and_found_messages).
--
-- Bug
-- ---
-- migration 115's lfm_select policy is:
--
--   FOR SELECT USING (
--       EXISTS (
--           SELECT 1 FROM lost_and_found lf
--           WHERE lf.id::text = lost_and_found_id
--             AND (auth.uid()::text = lf.reporter_id::text
--                  OR auth.uid()::text = lf.driver_id::text)
--       )
--   )
--
-- That EXISTS subquery's own read of `lost_and_found` is itself subject to
-- lost_and_found's RLS (migrations 69/69a), whose SELECT policy only
-- allows `auth.uid() = reporter_id` -- it says nothing about drivers. So
-- when a DRIVER queries lost_and_found_messages, the subquery's scan of
-- lost_and_found is filtered down to zero rows by lost_and_found's own
-- policy *before* the "OR auth.uid() = lf.driver_id" branch ever gets a
-- chance to evaluate -- the EXISTS comes back false and the driver sees no
-- messages on a case they are legitimately assigned to. lfm_insert has the
-- identical bug in its own EXISTS subquery.
--
-- This is real Postgres RLS behavior (a policy's subquery is filtered by
-- the referenced table's own policies), not an environment quirk --
-- confirmed via a new RLS role-level test
-- (backend/tests/rls/test_lost_and_found_rls.py,
-- test_driver_on_case_can_select_messages) run against a real Postgres:
-- the driver-visibility half of lfm_select has never actually worked
-- since migration 115 shipped it.
--
-- Real-world impact is likely limited today -- this repo's actual
-- lost-and-found chat feature is expected to go through the FastAPI
-- backend using the Supabase service_role key, which bypasses RLS
-- entirely -- but it is a real gap in the defense-in-depth layer RLS
-- exists for, and would matter the moment any client queries
-- lost_and_found_messages directly (e.g. via supabase-js) with a driver's
-- own JWT.
--
-- Fix
-- ---
-- A SECURITY DEFINER helper function looks up lost_and_found's
-- reporter_id/driver_id running as the function owner (bypassing RLS for
-- that one lookup), so lfm_select/lfm_insert no longer depend on the
-- caller's own visibility into lost_and_found. auth.uid() still resolves
-- correctly inside the function: request.jwt.claims is a session-level GUC
-- (set via set_config(..., false) -- not transaction-local), unaffected by
-- the role switch a SECURITY DEFINER call makes for privilege/RLS
-- purposes. Money-function safety conventions applied even though this
-- isn't a money path: STABLE, SECURITY DEFINER, pinned search_path,
-- EXECUTE revoked from PUBLIC then explicitly granted to the three roles
-- that actually need it (mirrors the auth.uid()/auth.role()/auth.jwt()
-- shim's own access pattern, not the stricter service-role-only pattern
-- used for money RPCs, since anon/authenticated must be able to call this
-- from their own RLS-governed queries).
--
-- Bucket names/response shape of the two policies are unchanged --
-- reporter behavior (already correct) is identical before and after; only
-- the driver branch, which never worked, starts working. No frontend
-- contract change.
--
-- Rollback:
--   DROP POLICY IF EXISTS lfm_select ON lost_and_found_messages;
--   DROP POLICY IF EXISTS lfm_insert ON lost_and_found_messages;
--   -- then re-run migration 115's original lfm_select/lfm_insert bodies
--   -- verbatim (lines 56-84 of 115_lost_and_found_chat.sql) to restore the
--   -- pre-fix (buggy) behavior.
--   DROP FUNCTION IF EXISTS public.is_party_to_lost_and_found_case(text);
--   -- No schema/table change, no data written either direction.

CREATE OR REPLACE FUNCTION public.is_party_to_lost_and_found_case(p_case_id text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1 FROM lost_and_found lf
        WHERE lf.id::text = p_case_id
          AND (
              auth.uid()::text = lf.reporter_id::text
           OR auth.uid()::text = lf.driver_id::text
          )
    );
$$;

COMMENT ON FUNCTION public.is_party_to_lost_and_found_case(text) IS
    'Reporter/driver membership check for a lost_and_found case, used by '
    'lost_and_found_messages'' RLS policies. SECURITY DEFINER so the lookup '
    'is not itself filtered by lost_and_found''s own RLS (migration 412 -- '
    'fixes a bug where a driver could never satisfy lfm_select/lfm_insert).';

REVOKE ALL ON FUNCTION public.is_party_to_lost_and_found_case(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.is_party_to_lost_and_found_case(text) TO anon, authenticated, service_role;

DROP POLICY IF EXISTS lfm_select ON lost_and_found_messages;
CREATE POLICY lfm_select ON lost_and_found_messages
    FOR SELECT USING (public.is_party_to_lost_and_found_case(lost_and_found_id));

DROP POLICY IF EXISTS lfm_insert ON lost_and_found_messages;
CREATE POLICY lfm_insert ON lost_and_found_messages
    FOR INSERT WITH CHECK (
        sender_id IS NOT NULL
        AND auth.uid()::text = sender_id::text
        AND sender_role != 'system'
        AND public.is_party_to_lost_and_found_case(lost_and_found_id)
    );

NOTIFY pgrst, 'reload schema';
