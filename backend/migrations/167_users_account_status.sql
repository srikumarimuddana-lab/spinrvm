-- 167_users_account_status.sql
--
-- Purpose:
--   Add the rider account-moderation columns the app already expects but that
--   were never created. routes/rides.py blocks booking for status in
--   ('banned','suspended') and routes/admin/users.py writes `status` +
--   `updated_at` — but `users` had no such columns, so the admin suspend/ban
--   call 503'd (42703) and the booking gate silently treated everyone as
--   'active'. This adds:
--     status            active | suspended | banned | pending_deletion
--     status_reason     why the account was suspended/banned (moderation note)
--     status_changed_at when status last changed
--     status_changed_by admin id that made the change
--     suspended_until   optional auto-lift time for a *temporary* suspension
--     updated_at        generic row-touched timestamp (also written elsewhere)
--
--   'pending_deletion' is included in the CHECK because the PIPEDA DSAR flow
--   (routes/users.py DELETE /account, migration 66) sets it.
--
--   ADD COLUMN ... DEFAULT 'active' is a metadata-only change on PG11+ (no table
--   rewrite); the CHECK is added NOT VALID then VALIDATE'd so it never blocks
--   writes; the status index is partial + CONCURRENTLY (users is hot). The
--   runner detects CONCURRENTLY and applies the file in autocommit mode.
--
-- RLS: users already has RLS; adding columns does not change it. The service
-- role (backend) bypasses RLS; only the backend mutates status.
--
-- Rollback:
--   ALTER TABLE users
--     DROP COLUMN IF EXISTS status, DROP COLUMN IF EXISTS status_reason,
--     DROP COLUMN IF EXISTS status_changed_at, DROP COLUMN IF EXISTS status_changed_by,
--     DROP COLUMN IF EXISTS suspended_until, DROP COLUMN IF EXISTS updated_at;
--   DROP INDEX IF EXISTS idx_users_status;
--   (constraint users_status_check is dropped with the status column.)

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status            TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS status_reason     TEXT,
    ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS status_changed_by TEXT,
    ADD COLUMN IF NOT EXISTS suspended_until   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_check;
ALTER TABLE users
    ADD CONSTRAINT users_status_check
    CHECK (status IN ('active', 'suspended', 'banned', 'pending_deletion')) NOT VALID;
ALTER TABLE users VALIDATE CONSTRAINT users_status_check;

-- Admin "suspended / banned" queue filter — partial index keeps it tiny.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_status
    ON users (status) WHERE status <> 'active';

COMMENT ON COLUMN users.status IS
    'Account moderation state: active | suspended | banned | pending_deletion.';
COMMENT ON COLUMN users.suspended_until IS
    'For a temporary suspension: when it auto-lifts. NULL = indefinite (admin must reactivate).';

NOTIFY pgrst, 'reload schema';
