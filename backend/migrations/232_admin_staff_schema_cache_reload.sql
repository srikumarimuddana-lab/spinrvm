-- 232_admin_staff_schema_cache_reload.sql
--
-- INCIDENT FIX: admin staff cannot log in / enroll MFA.
--
-- POST /api/admin/auth/mfa/enroll (and the forced-enrollment login flow) fails
-- in production with:
--   PGRST204 "Could not find the 'mfa_secret_pending' column of 'admin_staff'
--             in the schema cache"
--
-- Root cause: the columns were added by 52_admin_staff_mfa.sql and
-- 56_admin_staff_security_columns.sql, but neither migration issued
-- `NOTIFY pgrst, 'reload schema';`. The columns exist in Postgres, but
-- PostgREST (which the backend's supabase-py client talks to) still serves a
-- stale schema cache and rejects any write that references them. Migrations
-- 52/56 are already merged and are append-only, so the reload is issued here.
--
-- The ADD COLUMN IF NOT EXISTS statements below are idempotent belt-and-braces:
-- they re-assert the expected admin_staff shape in case 52/56 never ran on a
-- given environment, and are no-ops where the columns already exist. The single
-- NOTIFY at the end reloads the entire PostgREST schema cache, so every stale
-- admin_staff column (MFA + security) becomes visible at once.
--
-- Rollback plan:
--   Nothing to roll back — this migration only re-asserts existing columns and
--   reloads the PostgREST schema cache. It creates no new schema of its own.
--   (Dropping the underlying columns is 52/56's rollback, not this file's.)
--
-- Forward-compatible: all columns are nullable/defaulted; NOTIFY is a no-op if
-- the cache is already current. Safe to run against live production traffic.

ALTER TABLE admin_staff
  ADD COLUMN IF NOT EXISTS mfa_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS mfa_secret         TEXT,
  ADD COLUMN IF NOT EXISTS mfa_secret_pending TEXT,
  ADD COLUMN IF NOT EXISTS mfa_backup_codes   JSONB,
  ADD COLUMN IF NOT EXISTS locked_until       TIMESTAMPTZ DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS allowed_ips        JSONB       DEFAULT '[]'::jsonb;

-- Force PostgREST to drop its stale schema cache and re-introspect admin_staff.
NOTIFY pgrst, 'reload schema';
