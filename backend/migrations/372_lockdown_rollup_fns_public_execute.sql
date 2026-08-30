-- 372_lockdown_rollup_fns_public_execute.sql
--
-- Purpose:
--   Close a privilege gap in migrations 370_driver_earnings_rollup_fn.sql and
--   371_route_gap_latest_captures_fn.sql. Both end with:
--
--       REVOKE EXECUTE ON FUNCTION ... FROM anon, authenticated;
--
--   and both state, in their own comments, that this exists "so a leaked
--   anon/authenticated key cannot" read the data. It does not achieve that.
--
--   A newly created function carries a default EXECUTE grant to PUBLIC.
--   Revoking from anon and authenticated by name leaves that PUBLIC grant
--   intact, and both roles inherit EXECUTE through it. Confirmed on production
--   after 370/371 were applied — proacl was:
--
--       =X/postgres | postgres=X/postgres | service_role=X/postgres
--        ^^^^^^^^^^ the leading "=X" is the PUBLIC grant
--
--   and has_function_privilege('anon', ..., 'EXECUTE') returned true for both.
--   By contrast every function swept by migration 354 shows the correct shape:
--
--       postgres=X/postgres | service_role=X/postgres
--
--   Both functions are SECURITY DEFINER, so they bypass RLS. Left as-is they
--   were callable as PostgREST RPC endpoints with the anon key that ships in
--   the mobile apps, exposing fleet-wide driver earnings
--   (admin_driver_earnings_rollup) and per-ride location timing
--   (route_gap_latest_captures, PIPEDA-relevant).
--
--   This migration applies migration 354's exact remedy to the two functions:
--   REVOKE ... FROM PUBLIC, anon, authenticated, then GRANT to service_role.
--
-- Why a new migration rather than fixing 370/371 in place:
--   Both are already applied and their sha256 is recorded in
--   schema_migrations. run_migrations.py refuses to apply a file whose
--   checksum differs from the applied one, so editing them would hard-fail the
--   next run. Migrations are append-only (backend/migrations/CLAUDE.md).
--
-- Idempotent: REVOKE/GRANT are declarative end-state, safe to re-run.
--
-- Rollback (not advised — restores anon-executable SECURITY DEFINER fns):
--   GRANT EXECUTE ON FUNCTION public.admin_driver_earnings_rollup(text[]) TO PUBLIC;
--   GRANT EXECUTE ON FUNCTION public.route_gap_latest_captures(uuid[])    TO PUBLIC;

REVOKE EXECUTE ON FUNCTION public.admin_driver_earnings_rollup(text[]) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_driver_earnings_rollup(text[]) TO service_role;

REVOKE EXECUTE ON FUNCTION public.route_gap_latest_captures(uuid[]) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.route_gap_latest_captures(uuid[]) TO service_role;

-- PostgREST caches the schema; grant changes need a reload to take effect.
NOTIFY pgrst, 'reload schema';
