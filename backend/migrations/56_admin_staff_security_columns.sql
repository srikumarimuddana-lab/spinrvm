-- 56_admin_staff_security_columns.sql
-- A-P3-2: locked_until — 24-hour account lockout timestamp (informational mirror of Redis state).
-- A-P3-3: allowed_ips  — optional JSONB array of CIDR strings; empty = no restriction.
--
-- Rollback plan:
--   ALTER TABLE admin_staff DROP COLUMN IF EXISTS locked_until;
--   ALTER TABLE admin_staff DROP COLUMN IF EXISTS allowed_ips;
--
-- Forward-compatible: both columns are nullable/defaulted so existing rows
-- are unaffected and in-flight requests continue to work during migration.

ALTER TABLE admin_staff
    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS allowed_ips  JSONB        DEFAULT '[]'::jsonb;

-- Fast lookup when checking lockout status on login (A-P3-2).
CREATE INDEX IF NOT EXISTS idx_admin_staff_locked_until
    ON admin_staff (locked_until)
    WHERE locked_until IS NOT NULL;

COMMENT ON COLUMN admin_staff.locked_until IS
    'Timestamp until which this account is locked. NULL = not locked. '
    'Set by the backend when _LOGIN_MAX_FAILURES is reached (A-P3-2). '
    'Redis is the authoritative source; this column is kept in sync for '
    'audit/dashboard visibility.';

COMMENT ON COLUMN admin_staff.allowed_ips IS
    'Optional JSONB array of CIDR strings (e.g. ["10.0.0.0/8", "203.0.113.5/32"]). '
    'Empty array = no IP restriction. Non-empty = login is rejected unless client IP '
    'falls within at least one listed network (A-P3-3).';
