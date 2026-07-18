-- 233_admin_staff_last_activity_at.sql
--
-- INCIDENT FIX: admin_staff.last_activity_at is written on every admin request
-- (dependencies/__init__.py::_verify_admin_payload, for the 30-minute idle
-- timeout) but the column was never added by any migration. Production logs:
--   PGRST204 "Could not find the 'last_activity_at' column of 'admin_staff'
--             in the schema cache"
-- The write is soft-handled (warning + continue), so it did not hard-fail
-- requests, but it (a) spams the error log every request and (b) silently
-- disables the idle-timeout security control, since last_activity_at stays NULL
-- forever and the timeout check is skipped.
--
-- Add the column and reload the PostgREST schema cache in the same file (the
-- repo convention — see 232 and the many ADD COLUMN + NOTIFY migrations).
--
-- No index: the column is only ever read/written by admin_staff primary key
-- (id) inside _verify_admin_payload; there is no WHERE/ORDER BY on it.
--
-- Rollback plan:
--   ALTER TABLE admin_staff DROP COLUMN IF EXISTS last_activity_at;
--
-- Forward-compatible: nullable, no default — existing rows are unaffected and
-- the idle-timeout check treats NULL as "no recorded activity yet" (skips).

ALTER TABLE admin_staff
  ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ;

COMMENT ON COLUMN admin_staff.last_activity_at IS
  'Last time this staff member made an authenticated admin request. Updated on '
  'every request by _verify_admin_payload; drives the 30-minute idle-session '
  'timeout. NULL = no activity recorded yet (timeout check skipped).';

-- Reload PostgREST's schema cache so the REST layer (supabase-py) sees the
-- new column immediately instead of returning PGRST204.
NOTIFY pgrst, 'reload schema';
