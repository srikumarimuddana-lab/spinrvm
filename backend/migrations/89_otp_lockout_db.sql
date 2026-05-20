-- Migration: 89_otp_lockout_db.sql
-- Rollback: ALTER TABLE users DROP COLUMN IF EXISTS otp_locked_until;
--           ALTER TABLE users DROP COLUMN IF EXISTS otp_fail_count;
--
-- Purpose: persist OTP brute-force state in Postgres so a Redis restart
-- cannot grant an attacker a fresh 5-attempt window mid-lockout.
-- Redis remains the fast path; these columns are the authoritative source.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS otp_locked_until  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS otp_fail_count    SMALLINT NOT NULL DEFAULT 0;

-- Index so the lockout check (SELECT otp_locked_until FROM users WHERE phone = ?)
-- hits the existing phone index rather than a seq-scan.
-- The phone column index already exists (from migration 01); no new index needed.

COMMENT ON COLUMN users.otp_locked_until IS
    'UTC timestamp after which OTP attempts are re-allowed. NULL = not locked.';
COMMENT ON COLUMN users.otp_fail_count IS
    'Running count of consecutive OTP failures within the current window.';
