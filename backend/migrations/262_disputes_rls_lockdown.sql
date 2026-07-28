-- Migration 262: disputes append-only DELETE block (B2).
-- =============================================================================
-- Correction of an earlier draft of this migration, caught by review before
-- merge: the draft assumed migration 10's "Admin full access disputes"
-- (FOR ALL TO authenticated) was still the live policy and tried to replace
-- it with enumerated SELECT/UPDATE. That's stale — migration 142 already
-- superseded it months ago:
--   - Dropped "Admin full access disputes".
--   - Created "Admin read disputes" (SELECT only, role-checked) and
--     "Rider read own disputes" (SELECT only, own-row).
--   - REVOKEd INSERT/UPDATE/DELETE/TRUNCATE from `authenticated` entirely;
--     GRANTed SELECT only.
--   - Scrubbed the `user_name` column to '' and stopped writing it (PIPEDA).
-- So today, `authenticated` already has zero write access to `disputes` at
-- both the RLS and grant layers — the draft's SELECT/UPDATE policies would
-- have (a) failed to apply at all (duplicate policy name collision with
-- 142's "Admin read disputes") and (b) even if renamed, would have
-- *reopened* UPDATE for `authenticated` admins, a regression from 142's
-- intentionally tighter baseline (142's header: "backend mobile/admin
-- traffic is always mediated by the service_role client — the apps never
-- talk to PostgREST directly").
--
-- The one genuine gap 142 left: it only revoked/restricted `anon` and
-- `authenticated`. `service_role` still bypasses RLS by design (correctly —
-- the backend needs to INSERT on create and UPDATE on resolve) and nothing
-- stops a `service_role` caller (i.e. a future backend bug) from calling
-- DELETE on `disputes`. Disputes are a regulated financial/complaint record;
-- silently allowing deletion — even only via a backend bug, since no live
-- code path calls it today — breaks the ability to prove what was disputed
-- and how it was resolved. This migration closes exactly that gap, the same
-- defense-in-depth pattern already shipped for `audit_logs` in migration 51.
--
-- Confirmed via grep: no live code path deletes disputes — routes/disputes.py
-- only inserts (create) and updates (resolve); the one delete_many("disputes",
-- ...) call in the codebase lives in routes/admin/support.py, which is dead
-- code never imported/mounted by server.py or features.py.
--
-- Rollback: DROP TRIGGER disputes_no_delete ON disputes; DROP FUNCTION
-- disputes_block_delete(). That fully restores the pre-262 state (migration
-- 142's SELECT-only lockdown, untouched by this migration). No data is
-- touched by this migration, so rollback is a pure trigger/function drop —
-- no data-cleanup step.
-- =============================================================================

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

COMMENT ON TABLE disputes IS
    'Rider-filed payment/ride disputes and their admin resolution. DELETE is blocked by trigger disputes_no_delete for every role including service_role (append-only for regulatory/audit purposes). RLS: SELECT only for authenticated (migration 142) — admins via role check, riders via own user_id. INSERT/UPDATE go through service_role only (backend); authenticated has no write grant at all (migration 142).';

NOTIFY pgrst, 'reload schema';
