-- Migration 262: disputes RLS lockdown (B2).
-- =============================================================================
-- Closes the RLS half of backlog item B2. Migration 10 created "Admin full
-- access disputes" as FOR ALL TO authenticated — that permits any
-- authenticated admin JWT (via PostgREST) to UPDATE *or DELETE* dispute rows.
-- Disputes are a regulated financial/complaint record (refund decisions,
-- resolution notes); silently allowing deletion breaks the ability to prove
-- what was disputed and how it was resolved, and per
-- backend/migrations/CLAUDE.md's own convention ("INSERT / UPDATE / DELETE
-- explicitly enumerated — never FOR ALL on user-writable tables") this
-- table was never brought into line. Pattern follows migration 51
-- (audit_logs lockdown): enumerated policies + a trigger that blocks DELETE
-- even for service_role, as defense-in-depth against a future backend bug
-- (confirmed today: no live code path deletes disputes — routes/disputes.py
-- only inserts (create) and updates (resolve); the one delete_many("disputes",
-- ...) call in the codebase lives in routes/admin/support.py, which is dead
-- code never imported/mounted by server.py or features.py).
--
-- Unlike audit_logs, disputes ARE legitimately updated (admin resolution,
-- Zoho ticket-id backfill) — so UPDATE stays allowed for admins, only DELETE
-- is blocked.
--
-- Rollback: drop trigger disputes_no_delete, DROP the "Admin read disputes"
-- and "Admin update disputes" policies, recreate "Admin full access
-- disputes" FOR ALL TO authenticated (as it was in migration 10), and
-- restore the previous GRANT/REVOKE surface (GRANT ALL ... TO authenticated
-- via the FOR ALL policy). No data is touched by this migration
-- (RLS/grants/trigger only), so rollback is a straightforward policy/trigger
-- swap with no data-cleanup step.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Replace FOR ALL with enumerated SELECT + UPDATE for admins. No INSERT
--    policy for `authenticated` — every legitimate insert (rider dispute
--    creation) goes through backend service_role, which bypasses RLS by
--    design (see "Service role bypass disputes" policy from migration 10,
--    left unchanged). No DELETE policy either — combined with the trigger
--    below, DELETE is blocked for every role, service_role included.
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "Admin full access disputes" ON disputes;

CREATE POLICY "Admin read disputes"
    ON disputes FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = auth.uid()::text
              AND users.role IN ('admin', 'super_admin')
        )
    );

CREATE POLICY "Admin update disputes"
    ON disputes FOR UPDATE
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = auth.uid()::text
              AND users.role IN ('admin', 'super_admin')
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = auth.uid()::text
              AND users.role IN ('admin', 'super_admin')
        )
    );

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Append-only-on-delete enforcement. A dispute's resolution history must
--    be provable after the fact; the only legitimate mutations are INSERT
--    (rider files it) and UPDATE (admin resolves it, Zoho ticket-id
--    backfill). This trigger extends the DELETE block to service_role too,
--    the same defense-in-depth posture as audit_logs_no_update in
--    migration 51.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION disputes_block_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'disputes is append-only — DELETE is not permitted (attempted on row %)',
        OLD.id
        USING ERRCODE = 'check_violation';
END;
$$;

DROP TRIGGER IF EXISTS disputes_no_delete ON disputes;
CREATE TRIGGER disputes_no_delete
    BEFORE DELETE ON disputes
    FOR EACH ROW
    EXECUTE FUNCTION disputes_block_delete();

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. PostgREST surface narrowing, matching the new policy set: anon never
--    sees disputes; authenticated gets SELECT + UPDATE only (both still
--    gated by the RLS policies above). Backend writes (INSERT) go through
--    service_role and bypass this grant layer entirely.
-- ─────────────────────────────────────────────────────────────────────────────
REVOKE ALL ON disputes FROM anon;
REVOKE INSERT, DELETE, TRUNCATE ON disputes FROM authenticated;
GRANT  SELECT, UPDATE ON disputes TO authenticated;

COMMENT ON TABLE disputes IS
    'Rider-filed payment/ride disputes and their admin resolution. DELETE is blocked by trigger disputes_no_delete (append-only for regulatory/audit purposes). INSERT goes through service_role only (backend); SELECT/UPDATE go through authenticated admins (RLS) or service_role.';

NOTIFY pgrst, 'reload schema';
