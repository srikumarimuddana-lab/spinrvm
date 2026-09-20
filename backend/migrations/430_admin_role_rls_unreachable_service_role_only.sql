-- migration 430: formalize the migration-142/416 admin-role RLS policies as
-- unreachable-by-design (service-role-only), on the 10 tables ACTION_ITEMS.md
-- C107 names plus `disputes` (migration 142's own 10th table, sharing the
-- identical pattern in the same file but not counted in that ticket's "10").
-- =============================================================================
-- What's wrong: migrations 142 and 416 gate admin SELECT access to these 11
-- tables via `EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
-- AND users.role IN ('admin', 'super_admin'))`. Migration 256
-- (`chk_users_role_not_admin`) added a CHECK constraint that blocks any
-- `users` row from ever holding `role='admin'`/`'super_admin'` again. Real
-- admin auth runs entirely through a separate identity model (`admin_staff` +
-- `_verify_admin_payload`, `backend/dependencies/__init__.py`) — a custom JWT
-- verified inside the FastAPI app, which never creates or touches a Supabase
-- Auth session. `admin_staff` itself (migration 18) has no column linking it
-- to a Supabase Auth user at all. Net effect: this policy is not just
-- currently-unreachable, it is structurally unreachable — there is no path,
-- today or with a naive "check admin_staff instead" swap, for `auth.uid()` to
-- ever match an admin. Confirmed empirically: production `users` has zero
-- rows with `role IN ('admin', 'super_admin')` (query run 2026-09-14).
--
-- Why this is safe to change: the backend's only Supabase client
-- (`backend/supabase_client.py`) always uses `SUPABASE_SERVICE_ROLE_KEY`,
-- which bypasses RLS entirely — these policies have never gated a single
-- real request. This is a defense-in-depth correctness fix, not a live
-- incident fix; see ACTION_ITEMS.md C107 for the full history.
--
-- What this does NOT touch: the separate rider/member "read own row" SELECT
-- policies on `disputes`, `corporate_members`, `corporate_member_allowances`,
-- and `corporate_allowance_requests` (auth.uid() = user_id, or via
-- corporate_members.user_id) are untouched — those use a different, still
-- fully reachable identity path unaffected by migration 256, and RLS ORs
-- multiple permissive policies together, so replacing an always-false clause
-- with another always-false clause changes nothing for them. The
-- GRANT SELECT ON <table> TO authenticated statements migrations 142/416
-- already put in place are also left untouched, since those 4 tables' "own
-- row" policies depend on that base grant to run at all.
--
-- What this does NOT fix: 7 more tables were found during C107's review with
-- the identical pattern (audit_logs, safety_incidents, driver_insurance_periods,
-- cloud_messages, push_tokens, document_requirements, plus document_requirements's
-- older role='admin'-only form) — two of them safety/regulatory-critical. Not
-- included here per explicit scope decision; tracked separately as C123.
--
-- Rollback plan: DROP each "<table> admin RLS unreachable (service role only)"
-- policy below and re-run migrations 142 §2 / 416 to restore the
-- role-IN-admin-super_admin policies verbatim. Zero data changes in this
-- migration — pure policy swap, instantly reversible, no PITR needed.
-- =============================================================================

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'disputes',
        'corporate_wallets',
        'corporate_wallet_transactions',
        'corporate_members',
        'corporate_member_allowances',
        'corporate_allowance_requests',
        'corporate_policies',
        'corporate_allowed_domains',
        'ride_payment_sources',
        'corporate_policy_evaluations',
        'corporate_accounts'
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS "Admin read %s" ON %I', t, t);
        EXECUTE format('DROP POLICY IF EXISTS "%s admin RLS unreachable (service role only)" ON %I', t, t);

        EXECUTE format(
            'CREATE POLICY "%s admin RLS unreachable (service role only)" ON %I '
            'FOR SELECT TO authenticated USING (false)',
            t, t
        );
    END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
