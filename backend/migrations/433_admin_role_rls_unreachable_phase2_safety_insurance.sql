-- migration 433: C123 phase 2 — same unreachable admin-role RLS pattern as
-- migrations 430/432, applied to the 2 remaining tables deliberately
-- deferred from phase 1 for their safety/regulatory sensitivity
-- (ACTION_ITEMS.md C123).
-- =============================================================================
-- What's wrong: `safety_incidents` and `driver_insurance_periods` gate
-- admin access on `users.role IN ('admin', 'super_admin')` / `= ANY
-- (ARRAY['admin','super_admin'])`. Migration 256 (`chk_users_role_not_admin`)
-- makes that role value permanently impossible to hold; a production check
-- confirms zero `users` rows currently hold it. Same root cause and same
-- "unreachable, not actively exploited" risk profile as migrations 430/432 —
-- the backend's only Supabase client always uses service_role (bypasses RLS
-- entirely), so neither policy gates a single real request today.
--
-- Verified against live production (soavhtdhefowwvforzwb) via `pg_policies`
-- immediately before writing this migration — both tables' policies below
-- match their migration-file text (94/64) exactly; no out-of-band drift.
--
-- `safety_incidents` gets the same straightforward treatment as phase 1:
-- its two broken admin policies ("Admin read/update safety_incidents" FOR
-- SELECT, "Admin update safety_incidents" FOR UPDATE) are each standalone —
-- no other policy's legitimate access is expressed in the same USING clause
-- — so each is replaced with an explicit USING (false) deny, same pattern
-- as migration 430/432. "Reporter can read own safety_incidents" (own-row)
-- and "Service role bypass safety_incidents" are untouched.
--
-- `driver_insurance_periods` is NOT a blanket deny, unlike every other table
-- in this fix family. Its one SELECT policy (`driver_insurance_periods_select`)
-- combines the driver's own legitimate self-read access with the broken
-- admin check in a single USING clause via OR:
--
--   driver_id = (SELECT id FROM drivers WHERE user_id = auth.uid()::text)
--   OR (SELECT role FROM users WHERE id = auth.uid()::text)
--        IN ('admin', 'super_admin')
--
-- Replacing this policy with `USING (false)` (phase 1's pattern) would also
-- deny the driver's own, currently-working read access to their own
-- insurance-period history — a functional regression on a regulatory audit
-- trail (SGI / Saskatchewan Transportation Act, 7-year retention,
-- CLAUDE.md's insurance-period rules), not just closing a dead path. The fix
-- here instead drops only the broken `OR <admin check>` clause, keeping the
-- driver-owned-row access exactly as it is today. The policy is recreated
-- with the same name and the same implicit PUBLIC role scope as the
-- original (no `TO <role>` clause in either version) — this migration
-- changes only the USING expression, nothing else about the policy's shape.
--
-- Rollback plan: drop each replacement policy and re-create the original
-- verbatim (text below, taken directly from each table's own migration file
-- — 94/64 — and confirmed to match production via the pg_policies check
-- above). Both are verified executable (unlike C123 phase 1's
-- document_requirements exception — no type-mismatch issue here):
--
--   DROP POLICY IF EXISTS "safety_incidents admin SELECT RLS unreachable (service role only)" ON safety_incidents;
--   CREATE POLICY "Admin read/update safety_incidents" ON safety_incidents FOR SELECT TO authenticated
--       USING (EXISTS (SELECT 1 FROM public.users WHERE public.users.id = auth.uid()::text
--                        AND public.users.role IN ('admin', 'super_admin')));
--
--   DROP POLICY IF EXISTS "safety_incidents admin UPDATE RLS unreachable (service role only)" ON safety_incidents;
--   CREATE POLICY "Admin update safety_incidents" ON safety_incidents FOR UPDATE TO authenticated
--       USING (EXISTS (SELECT 1 FROM public.users WHERE public.users.id = auth.uid()::text
--                        AND public.users.role IN ('admin', 'super_admin')));
--
--   DROP POLICY IF EXISTS driver_insurance_periods_select ON driver_insurance_periods;
--   CREATE POLICY driver_insurance_periods_select ON driver_insurance_periods
--       FOR SELECT USING (
--           driver_id = (SELECT id FROM drivers WHERE user_id = auth.uid()::text)
--           OR (SELECT role FROM users WHERE id = auth.uid()::text) IN ('admin', 'super_admin')
--       );
--
-- Zero data changes — pure RLS policy swap, instantly reversible via psql,
-- no PITR or second deploy needed.
-- =============================================================================

DROP POLICY IF EXISTS "Admin read/update safety_incidents" ON safety_incidents;
CREATE POLICY "safety_incidents admin SELECT RLS unreachable (service role only)"
    ON safety_incidents FOR SELECT TO authenticated USING (false);

DROP POLICY IF EXISTS "Admin update safety_incidents" ON safety_incidents;
CREATE POLICY "safety_incidents admin UPDATE RLS unreachable (service role only)"
    ON safety_incidents FOR UPDATE TO authenticated USING (false);

DROP POLICY IF EXISTS driver_insurance_periods_select ON driver_insurance_periods;
CREATE POLICY driver_insurance_periods_select ON driver_insurance_periods
    FOR SELECT USING (
        driver_id = (SELECT id FROM drivers WHERE user_id = auth.uid()::text)
    );

NOTIFY pgrst, 'reload schema';
