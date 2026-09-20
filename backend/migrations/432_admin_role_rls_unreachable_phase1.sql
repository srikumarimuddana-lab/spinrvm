-- migration 432: C123 phase 1 — same unreachable admin-role RLS pattern as
-- migration 430, applied to 4 more tables found during C107's review but
-- deliberately deferred (ACTION_ITEMS.md C123) pending explicit sign-off.
-- =============================================================================
-- What's wrong: 4 tables gate an admin-only policy on
-- `EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text AND
-- users.role IN ('admin', 'super_admin'))` (or the older, narrower
-- `role = 'admin'` form on document_requirements). Migration 256
-- (`chk_users_role_not_admin`) makes that role value permanently impossible
-- to hold, and a 2026-09-14 production check confirms zero `users` rows
-- currently hold it — same root cause and same "unreachable, not actively
-- exploited" risk profile as migration 430's 11 tables. The backend's only
-- Supabase client always uses service_role (bypasses RLS entirely), so none
-- of these policies gate a single real request today.
--
-- Verified against live production (soavhtdhefowwvforzwb) via `pg_policies`
-- immediately before writing this migration — all 4 policies below match
-- their migration-file text exactly; no out-of-band drift like C124's
-- corporate_accounts finding.
--
-- Two of the 4 tables (cloud_messages, document_requirements) have their
-- admin policy as FOR ALL rather than FOR SELECT, with no WITH CHECK clause
-- — the same missing-WITH-CHECK shape migration 416 fixed on corporate_accounts.
-- The fix below replaces FOR ALL with FOR ALL ... USING (false), which
-- Postgres also applies as the (absent) WITH CHECK for INSERT/UPDATE,
-- closing that secondary gap at the same time rather than leaving it as a
-- separate follow-up.
--
-- What this deliberately does NOT touch: `safety_incidents` and
-- `driver_insurance_periods` (C123's other 2 tables). Deferred to a
-- follow-up migration — both are safety/regulatory-sensitive, and
-- driver_insurance_periods' admin check is entangled in the same USING
-- clause as the driver's own legitimate self-read access (`driver_id = own
-- OR <broken admin check>`), so it needs a different, non-blanket-deny fix
-- rather than a straight copy of this pattern. See ACTION_ITEMS.md C123.
--
-- Rollback plan: drop each replacement policy and re-create the original
-- verbatim (text below, taken directly from each table's own migration
-- file — 06/51 — and confirmed to match production via the pg_policies
-- check above). This is verified executable for 3 of the 4 tables:
--
--   DROP POLICY IF EXISTS "audit_logs admin RLS unreachable (service role only)" ON audit_logs;
--   CREATE POLICY "Admin read audit_logs" ON audit_logs FOR SELECT TO authenticated
--       USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
--                        AND users.role IN ('admin', 'super_admin')));
--
--   DROP POLICY IF EXISTS "push_tokens admin RLS unreachable (service role only)" ON push_tokens;
--   CREATE POLICY "Admin read push_tokens" ON push_tokens FOR SELECT TO authenticated
--       USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
--                        AND users.role IN ('admin', 'super_admin')));
--
--   DROP POLICY IF EXISTS "cloud_messages admin RLS unreachable (service role only)" ON cloud_messages;
--   CREATE POLICY "Admin full access cloud_messages" ON cloud_messages FOR ALL TO authenticated
--       USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
--                        AND users.role IN ('admin', 'super_admin')));
--
-- document_requirements is the exception: its original policy text
-- (`public.users.id = auth.uid()`, comparing text to uuid with no cast) does
-- NOT execute if replayed today — reproduced directly, both by a
-- spinr-migration-reviewer pass and independently beforehand: a fresh
-- `CREATE POLICY` with this exact text raises `operator does not exist: text
-- = uuid` against the current schema, even though this text matches what's
-- stored in production's own pg_policies right now (see
-- docs/change-log/2026-09-20-c123-phase1-rls-unreachable-admin.md for the
-- full investigation; the mechanism that let it reach production in this
-- state is unresolved). Restoring it byte-for-byte is therefore not a valid
-- rollback step, and restoring it would add nothing anyway — that clause
-- could never grant access even when it could be created. The correct
-- rollback for this one table is simply:
--
--   DROP POLICY IF EXISTS "document_requirements admin RLS unreachable (service role only)" ON document_requirements;
--
-- which leaves the table on RLS's implicit default-deny for that policy
-- slot — functionally identical to the original (unreachable either way),
-- and the table's separate "Public read access for requirements" policy
-- (untouched by this migration) continues to work exactly as before.
--
-- Zero data changes — pure RLS policy swap. 3 of the 4 statements above are
-- instantly reversible via psql; document_requirements' rollback is the
-- one-line DROP above, not a restore. No PITR or second deploy needed for
-- any of the 4.
-- =============================================================================

DROP POLICY IF EXISTS "Admin read audit_logs" ON audit_logs;
CREATE POLICY "audit_logs admin RLS unreachable (service role only)"
    ON audit_logs FOR SELECT TO authenticated USING (false);

DROP POLICY IF EXISTS "Admin read push_tokens" ON push_tokens;
CREATE POLICY "push_tokens admin RLS unreachable (service role only)"
    ON push_tokens FOR SELECT TO authenticated USING (false);

DROP POLICY IF EXISTS "Admin full access cloud_messages" ON cloud_messages;
CREATE POLICY "cloud_messages admin RLS unreachable (service role only)"
    ON cloud_messages FOR ALL TO authenticated USING (false);

DROP POLICY IF EXISTS "Admin full access for requirements" ON document_requirements;
CREATE POLICY "document_requirements admin RLS unreachable (service role only)"
    ON document_requirements FOR ALL TO authenticated USING (false);

NOTIFY pgrst, 'reload schema';
