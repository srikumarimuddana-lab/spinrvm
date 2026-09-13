-- Migration 416: corporate_accounts admin RLS policy — add super_admin,
-- close the same FOR ALL/no-WITH-CHECK gap migration 142 already closed on
-- its 9 sibling corporate tables.
-- =============================================================================
-- Migration 142 replaced the "Admin full access <table>" FOR ALL TO
-- authenticated policy (no WITH CHECK, role = 'admin' only) that migration 27
-- created on 9 corporate financial tables with a SELECT-only "Admin read
-- <table>" policy checking role IN ('admin', 'super_admin'), and stripped the
-- default anon/authenticated write grants down to SELECT-only for
-- `authenticated`.
--
-- corporate_accounts was never one of those 9 tables — its own admin policy
-- predates migration 27, created separately by migration 17 with the exact
-- same shape migration 142 fixed everywhere else: FOR ALL, no WITH CHECK,
-- role = 'admin' only. It was simply missed from that round of fixes and
-- still carries that original shape today, meaning:
--   (a) a super_admin-role authenticated JWT is denied entirely (SELECT
--       included) where an admin-role one is allowed — the parity gap this
--       migration was opened to close, confirmed by a new failing RLS test
--       this table never had before, and
--   (b) an admin-role authenticated JWT still has unrestricted PostgREST
--       INSERT/UPDATE/DELETE with no WITH CHECK clause — the same P0-class
--       gap migration 142 closed on the 9 siblings.
-- This migration applies migration 142's exact fix pattern (section 2) to
-- corporate_accounts only, so it now matches its 9 siblings' current,
-- already-fixed shape. It does NOT touch any of those 9 tables.
--
-- Note: backend mobile/admin traffic is always mediated by the service_role
-- client (backend/supabase_client.py is the only place this repo constructs
-- a Supabase client, always with SUPABASE_SERVICE_ROLE_KEY, which bypasses
-- RLS entirely) — grepped every backend reader/writer of corporate_accounts
-- for this change's Change Impact Log and found none going through
-- anon/authenticated PostgREST. As migration 142's own header noted for its
-- 9 tables, this policy exists for convention compliance and future
-- direct-read tooling, not because any current backend code path needs it.
--
-- Rollback plan (restores migration 17's original policy + grants verbatim):
--   DROP POLICY IF EXISTS "Admin read corporate_accounts" ON corporate_accounts;
--   GRANT INSERT, UPDATE, DELETE, TRUNCATE ON corporate_accounts TO authenticated;
--   GRANT ALL ON corporate_accounts TO anon;
--   CREATE POLICY "Admin full access corporate_accounts"
--       ON corporate_accounts FOR ALL TO authenticated
--       USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
--                        AND users.role = 'admin'));
-- =============================================================================

DROP POLICY IF EXISTS "Admin full access corporate_accounts" ON corporate_accounts;

CREATE POLICY "Admin read corporate_accounts"
    ON corporate_accounts FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = auth.uid()::text
              AND users.role IN ('admin', 'super_admin')
        )
    );

-- Strip default anon access and write access from authenticated (backend
-- uses service_role for every read and write today) — same as migration
-- 142's per-table REVOKE/GRANT block.
REVOKE ALL ON corporate_accounts FROM anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON corporate_accounts FROM authenticated;
GRANT SELECT ON corporate_accounts TO authenticated;

NOTIFY pgrst, 'reload schema';
