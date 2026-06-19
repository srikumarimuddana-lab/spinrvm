-- Migration 175: add users.sessions_invalid_before watermark for Firebase session revocation
--
-- Rollback: ALTER TABLE users DROP COLUMN IF EXISTS sessions_invalid_before;
--
-- Why: Firebase ID tokens are minted by Firebase and never carry our
-- token_version claim, so the token_version revocation gate cannot enforce
-- /auth/logout-all for Firebase-authenticated riders. This nullable timestamp
-- is set to now() by /auth/logout-all; the Firebase auth path rejects any ID
-- token whose auth_time predates it. NULL means "never invalidated", and the
-- add is forward-compatible with in-flight traffic.
-- No RLS change: column lives on the existing users table (service role bypasses
-- RLS by design; the column is read only on the backend auth path). No index
-- needed: it is read as part of the existing primary-key users lookup.
--
-- Backfill: users with token_version > 0 already invoked /auth/logout-all under
-- the old Firebase gate (which rejected their Firebase tokens via
-- _token_version_mismatch). Leaving their watermark NULL would silently
-- un-revoke those sessions after this migration. Stamp the watermark with the
-- migration time so any Firebase token from a sign-in before now stays revoked;
-- it can only over-revoke (force one re-sign-in for users who already chose
-- "log out everywhere"), never under-revoke.
--
-- Rolling-deploy gap (IMPORTANT): the backfill below is a point-in-time
-- snapshot. During a rolling deploy an OLD backend instance can still process
-- /auth/logout-all or the refresh-token-reuse cascade AFTER this UPDATE runs and
-- bump users.token_version WITHOUT stamping sessions_invalid_before (old code
-- doesn't know the column), leaving a token_version>0 / watermark-NULL row that
-- the new Firebase path would treat as not-revoked. New backend code always
-- stamps the watermark alongside every token_version bump, so the window is
-- bounded to this one deploy. To close it, the backfill is idempotent — re-run
-- the UPDATE below (or this whole migration) as the FINAL deploy step, once
-- 100% of instances are on the new code. We chose this over a code "fail closed
-- on NULL watermark" gate because that would force-logout legitimately-active
-- Firebase users mid-deploy with no clean self-service recovery path (a
-- re-login cannot safely distinguish a stolen pre-logout token from a fresh one
-- when Firebase's own revocation has not propagated).

ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_invalid_before TIMESTAMPTZ;

UPDATE users
SET sessions_invalid_before = NOW()
WHERE COALESCE(token_version, 0) > 0
  AND sessions_invalid_before IS NULL;
