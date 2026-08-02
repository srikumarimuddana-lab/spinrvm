-- 274_data_transfer_export_jobs_admin_id_no_fk.sql
-- Fix: data_transfer_export_jobs.requested_by_admin_id can never actually
-- satisfy its `REFERENCES users(id)` FK for a real admin caller.
--
-- Rollback: forward-only fix, nothing to revert data-wise (DROP CONSTRAINT
-- has no down-migration file per convention). To undo, add a NEW migration
-- that re-runs the original `REFERENCES users(id)` DDL; never hand-edit
-- this file or restore a dropped constraint by re-applying 272 in reverse.
--
-- Same bug class as migrations 213 (kyb_reviewed_by), 214
-- (corporate_wallet_transactions.actor_user_id), and 270 (this exact FK
-- pattern on admin_export_approval_requests.requested_by/decided_by):
-- platform-admin identity lives in the JWT / `admin_staff` table, not
-- `users` -- `admin["id"]` from `get_admin_user` is an `admin_staff.id` or
-- an env-var-creds sentinel ("admin-001"), neither of which is guaranteed
-- (or, in production, ever actually) present in `users`. Confirmed live:
-- SELECT id FROM users WHERE id IN (SELECT id FROM admin_staff) returns
-- zero rows in production.
--
-- Every real call to POST /api/admin/data-transfer/export has therefore
-- hit Postgres 23503 "insert or update on table ...violates foreign key
-- constraint" on the job-row insert, caught by the route's generic
-- except-Exception handler and surfaced to the admin as a 503 "Could not
-- record export job" -- reported by a real admin as "export failed,
-- internal server error" (the browser console showed the true 503, not
-- 500). Confirmed via direct query: zero rows have ever existed in
-- data_transfer_export_jobs, for anyone, since the table was created.
--
-- Fix: drop the FK. No type change needed (column is already TEXT).

BEGIN;

ALTER TABLE data_transfer_export_jobs
    DROP CONSTRAINT IF EXISTS data_transfer_export_jobs_requested_by_admin_id_fkey;

COMMENT ON COLUMN data_transfer_export_jobs.requested_by_admin_id IS
    'Platform-admin id (admin_staff.id, or the "admin-001"/"break-glass" '
    'env-var-creds sentinels) — no FK, since admin identity is not a users '
    'row (CLAUDE.md JWT trust model; same pattern as migrations 213/214/270).';

COMMIT;
