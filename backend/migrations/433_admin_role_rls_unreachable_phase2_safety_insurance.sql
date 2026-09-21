-- migration 433: C123 phase 2 — same unreachable admin-role RLS pattern as
-- migrations 430/432, applied to the tables deliberately deferred from
-- phase 1 for their safety/regulatory sensitivity (ACTION_ITEMS.md C123),
-- plus 2 direct siblings found during this migration's own investigation.
-- =============================================================================
-- What's wrong: `safety_incidents`, `driver_insurance_periods`,
-- `driver_insurance_period_corrections`, and `driver_period_distances` all
-- gate admin access on `users.role IN ('admin', 'super_admin')` / `= ANY
-- (ARRAY['admin','super_admin'])`. Migration 256 (`chk_users_role_not_admin`)
-- makes that role value permanently impossible to hold; a production check
-- confirms zero `users` rows currently hold it. Same root cause and same
-- "unreachable, not actively exploited" risk profile as migrations 430/432 —
-- the backend's only Supabase client always uses service_role (bypasses RLS
-- entirely), so none of these policies gate a single real request today.
--
-- Verified against live production (soavhtdhefowwvforzwb) via `pg_policies`
-- immediately before writing this migration — all 4 tables' policies below
-- match their migration-file text (94/64/355/249) exactly; no out-of-band
-- drift.
--
-- `driver_insurance_period_corrections` (355) and `driver_period_distances`
-- (249) were not part of C123's original scope (which only named
-- `driver_insurance_periods` itself) — found while writing this migration,
-- since both migrations explicitly say they mirror `driver_insurance_periods`'
-- shape (355's own header comment: "confirm the new table's RLS/immutability
-- pattern matches theirs, don't diverge silently"), and indeed carry the
-- identical entangled-OR pattern described below. Included here rather than
-- filed as a separate follow-up: same fix, same table family, same
-- migration this change already touches.
--
-- `safety_incidents` gets the same straightforward treatment as phase 1:
-- its two broken admin policies ("Admin read/update safety_incidents" FOR
-- SELECT, "Admin update safety_incidents" FOR UPDATE) are each standalone —
-- no other policy's legitimate access is expressed in the same USING clause
-- — so each is replaced with an explicit USING (false) deny, same pattern
-- as migration 430/432. "Reporter can read own safety_incidents" (own-row)
-- and "Service role bypass safety_incidents" are untouched.
--
-- `driver_insurance_periods`, `driver_insurance_period_corrections`, and
-- `driver_period_distances` are NOT a blanket deny, unlike every other table
-- in this fix family. Each has exactly one SELECT policy that combines the
-- driver's own legitimate self-read access (directly via driver_id, or via
-- the original_period_id -> driver_insurance_periods -> drivers chain for
-- corrections) with the broken admin check in a single USING clause via OR.
-- Example (driver_insurance_periods):
--
--   driver_id = (SELECT id FROM drivers WHERE user_id = auth.uid()::text)
--   OR (SELECT role FROM users WHERE id = auth.uid()::text)
--        IN ('admin', 'super_admin')
--
-- Replacing any of these three policies with `USING (false)` (phase 1's
-- pattern) would also deny the driver's own, currently-working read access
-- to their own insurance-period history — a functional regression on a
-- regulatory audit trail (SGI / Saskatchewan Transportation Act, 7-year
-- retention, CLAUDE.md's insurance-period rules), not just closing a dead
-- path. The fix for each instead drops only the broken `OR <admin check>`
-- clause, keeping the driver-owned-row access exactly as it is today. Each
-- policy is recreated with the same name and the same implicit PUBLIC role
-- scope as the original (no `TO <role>` clause in any of the three
-- originals) — this migration changes only the USING expression, nothing
-- else about any policy's shape.
--
-- Rollback plan: drop each replacement policy and re-create the original
-- verbatim (text below, taken directly from each table's own migration file
-- — 94/64/355/249 — and confirmed to match production via the pg_policies
-- check above). All 4 are verified executable (unlike C123 phase 1's
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
--   DROP POLICY IF EXISTS driver_insurance_period_corrections_select ON driver_insurance_period_corrections;
--   CREATE POLICY driver_insurance_period_corrections_select ON driver_insurance_period_corrections
--       FOR SELECT USING (
--           original_period_id IN (
--               SELECT id FROM driver_insurance_periods WHERE driver_id = (
--                   SELECT id FROM drivers WHERE user_id = auth.uid()::text
--               )
--           )
--           OR (SELECT role FROM users WHERE id = auth.uid()::text) IN ('admin', 'super_admin')
--       );
--
--   DROP POLICY IF EXISTS driver_period_distances_select ON driver_period_distances;
--   CREATE POLICY driver_period_distances_select ON driver_period_distances
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

DROP POLICY IF EXISTS driver_insurance_period_corrections_select ON driver_insurance_period_corrections;
CREATE POLICY driver_insurance_period_corrections_select ON driver_insurance_period_corrections
    FOR SELECT USING (
        original_period_id IN (
            SELECT id FROM driver_insurance_periods WHERE driver_id = (
                SELECT id FROM drivers WHERE user_id = auth.uid()::text
            )
        )
    );

DROP POLICY IF EXISTS driver_period_distances_select ON driver_period_distances;
CREATE POLICY driver_period_distances_select ON driver_period_distances
    FOR SELECT USING (
        driver_id = (SELECT id FROM drivers WHERE user_id = auth.uid()::text)
    );

NOTIFY pgrst, 'reload schema';
