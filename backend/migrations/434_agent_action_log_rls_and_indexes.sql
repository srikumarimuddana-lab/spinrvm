-- migration 434: fix agent_action_log's dead admin-read RLS policy (same
-- C107/C123 "unreachable admin-role RLS" pattern as migrations 430/432) and
-- add the two missing indexes flagged by spinr-migration-reviewer during the
-- 2026-09-20 /full-audit review of migration 429.
-- =============================================================================
-- What's wrong (RLS): migration 429's `agent_action_log_admin_read` policy
-- gates SELECT on `(auth.jwt() ->> 'role') = 'admin'`. Per root CLAUDE.md's
-- JWT trust model, real admin auth is a custom JWT_SECRET-signed token
-- verified inside FastAPI (admin_staff + _verify_admin_payload) that never
-- creates or touches a Supabase Auth session -- so `auth.jwt()` (a
-- Supabase/PostgREST built-in reading the *Supabase-issued* session JWT)
-- can never see `role: 'admin'` for a real admin request. This is the same
-- class of structurally-unreachable admin RLS that migration 430 (11 tables)
-- and 432 (4 more) exist to formalize elsewhere -- 429 shipped a fresh
-- instance of the pattern those two migrations are cleaning up. Not a
-- security hole (fails closed, doesn't leak data -- the actual admin read
-- path, backend/routes/admin/maintenance.py's GET /agent-actions, goes
-- through db_supabase.get_rows on the service-role client, which bypasses
-- RLS entirely and works fine), just a dead policy whose own comment
-- ("so the admin dashboard can list entries without going through the
-- service-role backend for every page load") describes a capability that
-- never worked as written -- there was also never a matching
-- `GRANT SELECT ON agent_action_log TO authenticated`, so even ignoring the
-- auth.jwt() problem, an `authenticated`-role PostgREST client would hit a
-- permission-denied before RLS is even evaluated.
--
-- Fix (RLS): same swap as migrations 430/432 -- replace the dead policy with
-- an explicit "unreachable (service role only)" marker policy, so this is
-- documented as an intentional, known state rather than a policy someone
-- might later assume is live and depend on.
--
-- What's wrong (indexes): backend/routes/admin/maintenance.py's
-- GET /agent-actions filters on agent_name, action_type, target_surface,
-- and outcome, ordered by created_at DESC. Migration 429 indexed
-- agent_name, target_surface, outcome, and risk_domain (the last two as
-- partial indexes) but missed action_type -- a real filter predicate on
-- this route -- and never added a plain created_at-only index for the
-- common no-filter call (ORDER BY created_at DESC with no WHERE clause
-- can't use any of the compound indexes' second column without a matching
-- leading-column predicate).
--
-- Fix (indexes): add both. Low urgency in practice (this is a low-volume,
-- append-only engineering-audit table, not a hot product path), but cheap
-- to close now rather than carry as a known gap. Plain (non-CONCURRENTLY)
-- CREATE INDEX, matching migration 429's own precedent for this table --
-- it's new/near-empty, so there's no existing-hot-table lock-contention
-- concern CONCURRENTLY exists to solve.
--
-- Rollback plan:
--   DROP POLICY IF EXISTS "agent_action_log admin RLS unreachable (service role only)" ON agent_action_log;
--   CREATE POLICY agent_action_log_admin_read ON agent_action_log
--       FOR SELECT USING ((auth.jwt() ->> 'role') = 'admin');
--   DROP INDEX IF EXISTS idx_agent_action_log_action_type;
--   DROP INDEX IF EXISTS idx_agent_action_log_created_at;
-- Zero data changes -- pure RLS policy swap plus two new indexes.
-- =============================================================================

DROP POLICY IF EXISTS agent_action_log_admin_read ON agent_action_log;
CREATE POLICY "agent_action_log admin RLS unreachable (service role only)"
    ON agent_action_log FOR SELECT TO authenticated USING (false);

CREATE INDEX IF NOT EXISTS idx_agent_action_log_action_type
    ON agent_action_log (action_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_action_log_created_at
    ON agent_action_log (created_at DESC);

NOTIFY pgrst, 'reload schema';
