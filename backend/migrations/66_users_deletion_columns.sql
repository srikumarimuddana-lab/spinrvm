-- Migration 66: add missing deletion tracking columns to users (PIPEDA DSAR fix).
-- =============================================================================
-- Problem: routes/users.py DELETE /account writes deletion_requested_at and
--   deletion_scheduled_at to the users table (R-P1-6), and migrations 56/57
--   index + query those columns inside purge_pii_retention(). However, no
--   previous migration ever added the columns to users, causing a 42703
--   "column does not exist" error on every account-deletion request and
--   breaking the DSAR purge function at runtime.
--
-- Rollback plan: ALTER TABLE users DROP COLUMN deletion_requested_at,
--   DROP COLUMN deletion_scheduled_at; DROP INDEX idx_users_pending_deletion;
-- =============================================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS deletion_scheduled_at TIMESTAMPTZ;

COMMENT ON COLUMN users.deletion_requested_at IS
    'Set when user invokes DELETE /account (R-P1-6). Marks start of the 30-day PIPEDA grace period. NULL = no deletion requested.';

COMMENT ON COLUMN users.deletion_scheduled_at IS
    'deletion_requested_at + 30 days. purge_pii_retention() anonymises PII once this timestamp has elapsed and status=pending_deletion.';

-- Partial index for the purge function WHERE clause.
-- Migration 56 attempted to create this index but failed because the column
-- did not exist; IF NOT EXISTS is safe in case some environment ran a manual
-- fix before this migration.
CREATE INDEX IF NOT EXISTS idx_users_pending_deletion
    ON users (deletion_scheduled_at)
    WHERE status = 'pending_deletion' AND deleted_at IS NULL;

NOTIFY pgrst, 'reload schema';
