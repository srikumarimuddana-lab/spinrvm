-- 278_compliance_export_events_admin_id_no_fk.sql
--
-- Same bug class as migrations 270 (admin_export_approval_requests) and 274
-- (data_transfer_export_jobs): compliance_export_events.admin_user_id was
-- declared `REFERENCES users(id)`, but admin identity in this codebase lives
-- in `admin_staff` (or an env-var-creds sentinel like "admin-001"/
-- "break-glass") — never in `users` (CLAUDE.md's JWT trust model). Every
-- real call to `_log_compliance_export()` (backend/routes/admin/
-- compliance.py) therefore violated this FK and silently failed — confirmed
-- live: `compliance_export_events` has zero rows, ever, despite every
-- Compliance report endpoint calling it on every successful export since
-- migration 263 created the table.
--
-- Found via a one-time repo-wide audit for the same `REFERENCES users(id)`
-- mistake on an admin-identity column (recommended as a follow-up in
-- docs/change-log/2026-08-01-data-transfer-export-jobs-fk-fix.md §10, after
-- fixing the second instance of this bug this session).
--
-- Rollback: Re-adding the FK would just reintroduce the always-failing-write
--   bug — there is nothing to roll back to. A plain `git revert` of this
--   migration file is safe for repo history; the live constraint drop
--   itself should not be undone.
--
-- Forward-compatible: drops one constraint, no column type change, no data
-- migration (the column was always populated correctly, just unable to
-- satisfy an FK that was never satisfiable by a real admin caller).

BEGIN;

ALTER TABLE compliance_export_events
    DROP CONSTRAINT IF EXISTS compliance_export_events_admin_user_id_fkey;

COMMENT ON COLUMN compliance_export_events.admin_user_id IS
    'Platform-admin id (admin_staff.id, or the "admin-001"/"break-glass" '
    'env-var-creds sentinels) — no FK, since admin identity is not a users '
    'row (CLAUDE.md JWT trust model; same pattern as migrations 213/214/'
    '270/274).';

COMMIT;
