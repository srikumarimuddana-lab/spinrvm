-- Migration 51: Remove user_email PII column from audit_logs (F-45)
-- Rollback: ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_email text;
--
-- The user_email column stored admin email addresses as plain text, violating
-- PIPEDA data minimisation. The new schema uses actor_id (opaque UUID) which
-- can be joined to admin_staff when a human-readable label is needed for display.
-- All write paths already use actor_id (see staff.py, auth.py). This migration
-- removes the now-unused PII column.

ALTER TABLE audit_logs DROP COLUMN IF EXISTS user_email;
