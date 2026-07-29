-- 270_export_approvals_admin_id_no_fk.sql
-- Fix: admin_export_approval_requests.requested_by/decided_by can never
-- actually satisfy their `REFERENCES users(id)` FK for a real admin caller.
--
-- Rollback: forward-only fix, nothing to revert data-wise (DROP CONSTRAINT
-- has no down-migration file per convention — see the note at the bottom of
-- this file). To undo, add a NEW migration that re-runs migration 268's
-- original `REFERENCES users(id)` DDL; never hand-edit this file or restore
-- a dropped constraint by re-applying 270 in reverse.
--
-- Same bug class as migrations 213 (kyb_reviewed_by) and 214
-- (corporate_wallet_transactions.actor_user_id): platform-admin identity
-- lives in the JWT / `admin_staff` table, not `users` — `admin["id"]` from
-- `get_admin_user`/`require_super_admin` is a string like "admin-001" (the
-- env-var-creds sentinel, no admin_staff row at all) or an `admin_staff.id`,
-- neither of which is guaranteed to exist in `users`. Migration 268 declared
-- `requested_by TEXT NOT NULL REFERENCES users(id)` and
-- `decided_by TEXT REFERENCES users(id)` — every real call to
-- `admin_export_approvals.create_request`/`approve`/`deny` from an actual
-- admin session hits Postgres 23503 "insert or update on table
-- ...violates foreign key constraint" (or, for admin-001/break-glass,
-- likely no matching row at all), surfacing to the caller as the generic
-- "could not upload the queue — DB operations failed" error observed on
-- PR #2819/#2820's shipped feature the first time a real admin used it.
--
-- Fix: drop both FKs. No type change needed (both columns are already
-- TEXT). The self-approval CHECK constraint (decided_by <> requested_by)
-- is untouched — it's a value comparison, not a referential one, so this
-- doesn't weaken the actual security property migration 268 exists for.

BEGIN;

ALTER TABLE admin_export_approval_requests
    DROP CONSTRAINT IF EXISTS admin_export_approval_requests_requested_by_fkey;

ALTER TABLE admin_export_approval_requests
    DROP CONSTRAINT IF EXISTS admin_export_approval_requests_decided_by_fkey;

COMMENT ON COLUMN admin_export_approval_requests.requested_by IS
    'Platform-admin id (admin_staff.id, or the "admin-001"/"break-glass" '
    'env-var-creds sentinels) — no FK, since admin identity is not a users '
    'row (CLAUDE.md JWT trust model; same pattern as migrations 213/214).';
COMMENT ON COLUMN admin_export_approval_requests.decided_by IS
    'Platform-admin id — no FK, same reason as requested_by above.';

COMMIT;
