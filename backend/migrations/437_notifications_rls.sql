-- 437_notifications_rls.sql
--
-- `notifications` (migration 48) is the one user-data table that never
-- shipped RLS. It has no ENABLE ROW LEVEL SECURITY and no policies, and no
-- later migration added any — confirmed by grepping every file in
-- backend/migrations/ for "ON notifications" / "TABLE notifications", which
-- matches only migration 48 itself. Every other user-data table follows the
-- pattern root CLAUDE.md mandates; this one was missed.
--
-- Found while adding the `audience` column (migration 436). Reported rather
-- than silently folded into that change, and deliberately kept as its own
-- migration so it can be rolled back independently of the audience work.
--
-- SAFETY — why enabling RLS here cannot break live traffic:
-- The backend talks to Supabase exclusively with the service-role key, which
-- bypasses RLS by design (see root CLAUDE.md's service-role convention), and
-- nothing else reads this table. Verified before writing this:
--   * rider-app and driver-app contain no Supabase client at all — they go
--     through the FastAPI backend (GET /notifications and friends).
--   * no `from('notifications')` / `from("notifications")` call exists in
--     any surface, including admin-dashboard.
-- So these policies are dormant-but-correct exactly like the rest of the
-- schema's (ACTION_ITEMS.md C108): they protect a future direct-PostgREST
-- path and catch regressions, rather than gating traffic that exists today.
-- Do not cite this migration as evidence the policies are live-enforced.
--
-- Admin access is deliberately NOT granted to `authenticated`, and no policy
-- is written for it at all. Migration 432 established that the
-- `users.role IN ('admin','super_admin')` check those older policies use is
-- unreachable — no admin ever holds a Supabase-issued session JWT, so
-- `auth.uid()` is never their id — and neutralised each one with an explicit
-- `USING (false)`. That replacement was needed there because a real admin
-- grant already existed and had to be overridden. Here there is nothing to
-- override: this table is getting its first policies. Adding a `USING (false)`
-- SELECT policy alongside the owner-read policy below would be a no-op
-- anyway, since Postgres OR-combines permissive policies for the same
-- command. So admins simply get no policy, which is the same outcome with
-- nothing misleading left in pg_policies. Admin reads go through the backend
-- on the service-role key, as they already do.
--
-- INSERT is intentionally omitted for `authenticated`: notification rows are
-- written only by features.py's _record_inbox_notification (service role). A
-- user must never be able to forge a notification into their own inbox, so
-- there is no owner INSERT policy. Per CLAUDE.md's RLS rules, SELECT/UPDATE/
-- DELETE are enumerated separately rather than granted as FOR ALL.
--
-- Rollback plan (safe at any time — reverts to today's no-RLS behavior):
--   DROP POLICY IF EXISTS "Users read own notifications" ON notifications;
--   DROP POLICY IF EXISTS "Users update own notifications" ON notifications;
--   DROP POLICY IF EXISTS "Users delete own notifications" ON notifications;
--   DROP POLICY IF EXISTS "Service role bypass notifications" ON notifications;
--   ALTER TABLE notifications DISABLE ROW LEVEL SECURITY;

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- Owner access. users.id is TEXT in this schema (see migration 48's own
-- comment), so auth.uid() is cast to text rather than compared as a uuid —
-- the uncast form is the exact defect migration 432 documented in
-- document_requirements' policy, which does not execute when replayed.
DROP POLICY IF EXISTS "Users read own notifications" ON notifications;
CREATE POLICY "Users read own notifications"
    ON notifications FOR SELECT TO authenticated
    USING (user_id = auth.uid()::text);

-- Mark-as-read / mark-all-read. WITH CHECK pins the row to the same owner so
-- an UPDATE cannot hand a row to another user.
DROP POLICY IF EXISTS "Users update own notifications" ON notifications;
CREATE POLICY "Users update own notifications"
    ON notifications FOR UPDATE TO authenticated
    USING (user_id = auth.uid()::text)
    WITH CHECK (user_id = auth.uid()::text);

-- Clear / clear-read.
DROP POLICY IF EXISTS "Users delete own notifications" ON notifications;
CREATE POLICY "Users delete own notifications"
    ON notifications FOR DELETE TO authenticated
    USING (user_id = auth.uid()::text);

-- Backend access.
DROP POLICY IF EXISTS "Service role bypass notifications" ON notifications;
CREATE POLICY "Service role bypass notifications"
    ON notifications FOR ALL TO service_role
    USING (true) WITH CHECK (true);
