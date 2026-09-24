-- migration 456: ACTION_ITEMS.md C129 — two findings on the financial-ledger
-- extension (migrations 286/59/151), missed by the C107/C123 unreachable-
-- admin-role sweeps (migrations 430/432/433).
-- =============================================================================
-- Finding 1: financial_event_entries_select (286) and reconciliation_
-- discrepancies' recon_admin_only (59) both gate access on
-- `(SELECT role FROM users WHERE id = auth.uid()::text) = 'admin'` — the
-- exact pattern migration 256's `chk_users_role_not_admin` CHECK constraint
-- made permanently unreachable in production (real admin identity lives in
-- `admin_staff`, never `users.role`). Migrations 430/432/433 systematically
-- replaced this pattern with an explicit `USING (false)` deny across every
-- other table carrying it; these two tables postdate/sit outside migration
-- 142's original table list and were never revisited by either later sweep.
-- Confirmed via `grep -l financial_event_entries\|reconciliation_discrepancies
-- backend/migrations/430_*.sql backend/migrations/432_*.sql
-- backend/migrations/433_*.sql` — zero matches in any of the three.
--
-- Why safe to change: the backend's only Supabase client always uses
-- SUPABASE_SERVICE_ROLE_KEY, which bypasses RLS entirely — these policies
-- have never gated a single real request. Defense-in-depth correctness fix,
-- not a live incident. See ACTION_ITEMS.md C129.
--
-- financial_event_entries_select was FOR SELECT (no explicit TO clause, but
-- anon is already REVOKEd at the table-grant layer by migration 286 itself);
-- replaced FOR SELECT TO authenticated, matching migration 430's convention
-- for the same table shape.
--
-- recon_admin_only was FOR ALL with NO explicit TO clause and, per this
-- entry's own investigation, NO accompanying REVOKE on this table (unlike
-- every other money table in this family) — so anon still holds Supabase's
-- default table-level grant here. Replaced FOR ALL with no TO clause
-- (applies to PUBLIC, matching the original's own scope) so RLS itself
-- denies anon too, rather than relying on a REVOKE this table never had.
--
-- Finding 2: subscription_payments (151) claims "Append-only ledger of
-- realized Spinr Pass payments" in its own COMMENT ON TABLE, but — unlike
-- its sibling financial_event_entries, which got a real UPDATE-blocking
-- trigger in the same migration that created it — no migration ever added
-- one. anon/authenticated INSERT/UPDATE/DELETE are already REVOKEd (151),
-- so this only matters against service_role, which bypasses RLS AND is
-- unaffected by table-level GRANT/REVOKE — only a trigger can constrain it.
-- Confirmed no production code path issues an UPDATE/DELETE (grepped
-- routes/, services/, utils/ for subscription_payments writes: INSERT only).
-- Unlike financial_event_entries_no_update (UPDATE-only, since a BEFORE
-- DELETE trigger would break its parent's ON DELETE CASCADE), subscription_
-- payments has no FK/cascade relationship to any parent row — confirmed by
-- grepping every migration touching this table (151/186/188) for a
-- REFERENCES/FOREIGN KEY on driver_id: none exists — so this trigger blocks
-- BOTH UPDATE and DELETE, making the table genuinely append-only end to end.
--
-- Rollback plan: DROP TRIGGER subscription_payments_no_mutate; DROP POLICY
-- on both tables and re-run migrations 286 §RLS / 59 to restore the
-- role='admin' policies verbatim. Zero data changes — pure policy/trigger
-- addition, instantly reversible, no PITR needed.
-- =============================================================================

DROP POLICY IF EXISTS financial_event_entries_select ON financial_event_entries;
DROP POLICY IF EXISTS "financial_event_entries admin RLS unreachable (service role only)" ON financial_event_entries;
CREATE POLICY "financial_event_entries admin RLS unreachable (service role only)"
    ON financial_event_entries
    FOR SELECT TO authenticated USING (false);

DROP POLICY IF EXISTS recon_admin_only ON reconciliation_discrepancies;
DROP POLICY IF EXISTS "reconciliation_discrepancies admin RLS unreachable (service role only)" ON reconciliation_discrepancies;
CREATE POLICY "reconciliation_discrepancies admin RLS unreachable (service role only)"
    ON reconciliation_discrepancies
    FOR ALL USING (false) WITH CHECK (false);

CREATE OR REPLACE FUNCTION _subscription_payments_immutable()
RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
    RAISE EXCEPTION
        'subscription_payments rows are append-only and cannot be modified or deleted';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'subscription_payments_no_mutate'
          AND tgrelid = 'subscription_payments'::regclass
    ) THEN
        CREATE TRIGGER subscription_payments_no_mutate
            BEFORE UPDATE OR DELETE ON subscription_payments
            FOR EACH ROW EXECUTE FUNCTION _subscription_payments_immutable();
    END IF;
END;
$$;

NOTIFY pgrst, 'reload schema';
