-- Migration 175: add users.sessions_invalid_before watermark for Firebase session revocation
--
-- Rollback: ALTER TABLE users DROP COLUMN IF EXISTS sessions_invalid_before;
--
-- Why: Firebase ID tokens are minted by Firebase and never carry our
-- token_version claim, so the token_version revocation gate cannot enforce
-- /auth/logout-all for Firebase-authenticated riders. This nullable timestamp
-- is set to now() by /auth/logout-all; the Firebase auth path rejects any ID
-- token whose auth_time predates it. NULL means "never invalidated", so no
-- backfill is required and the add is forward-compatible with in-flight traffic.
-- No RLS change: column lives on the existing users table (service role bypasses
-- RLS by design; the column is read only on the backend auth path). No index
-- needed: it is read as part of the existing primary-key users lookup.

ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_invalid_before TIMESTAMPTZ;
