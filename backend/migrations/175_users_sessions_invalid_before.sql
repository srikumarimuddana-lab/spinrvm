-- Migration 175: add users.sessions_invalid_before watermark for Firebase session revocation
--
-- Rollback:
--   DROP TRIGGER IF EXISTS trg_guard_session_revocation ON public.users;
--   DROP FUNCTION IF EXISTS public.guard_session_revocation_columns();
--   ALTER TABLE users DROP COLUMN IF EXISTS sessions_invalid_before;
--
-- Why: Firebase ID tokens are minted by Firebase and never carry our
-- token_version claim, so the token_version revocation gate cannot enforce
-- /auth/logout-all for Firebase-authenticated riders. This nullable timestamp
-- is set to now() by /auth/logout-all; the Firebase auth path rejects any ID
-- token whose auth_time predates it. NULL means "never invalidated", and the
-- add is forward-compatible with in-flight traffic. No index needed: it is read
-- as part of the existing primary-key users lookup.
--
-- Backfill: users with token_version > 0 already invoked /auth/logout-all under
-- the old Firebase gate (which rejected their Firebase tokens via
-- _token_version_mismatch). Leaving their watermark NULL would silently
-- un-revoke those sessions after this migration. Stamp the watermark with the
-- migration time so any Firebase token from a sign-in before now stays revoked;
-- it can only over-revoke (force one re-sign-in for users who already chose
-- "log out everywhere"), never under-revoke.
--
-- Trigger guard_session_revocation_columns does two jobs at the DB level so the
-- watermark is authoritative no matter which code path or role writes the row:
--   1. Rolling-deploy gap closure: whenever token_version INCREASES but the
--      caller did not set the watermark in the same UPDATE, stamp it to now().
--      This covers an OLD backend instance that bumps token_version (via
--      /auth/logout-all or the reuse cascade) during the deploy window without
--      knowing the new column — the runner records filenames and skips applied
--      migrations, so a re-run of this file would NOT fire; the trigger closes
--      the window for every bump, present and future, instead.
--   2. Tamper protection: token_version and sessions_invalid_before are
--      backend-managed. The users_update_self RLS policy lets an authenticated
--      user UPDATE their own row; without this guard they could clear or move
--      sessions_invalid_before via direct PostgREST access and bypass
--      logout-all. Any non-service role that changes either column is rejected.
-- SECURITY INVOKER (default) so current_user reflects the actual caller
-- (service_role for PostgREST backend writes; authenticated/anon for direct
-- PostgREST). Role membership is checked with pg_has_role (not a literal name
-- list) so it is robust to the connection user — service_role directly, or
-- postgres/superuser/pooler users that are members of it — and it is guarded by
-- a pg_roles EXISTS check so the trigger no-ops on a vanilla Postgres (CI
-- containers without Supabase's service_role) instead of erroring.

ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_invalid_before TIMESTAMPTZ;

-- Backfill BEFORE the trigger is installed, so the one-time snapshot can never
-- trip the tamper guard regardless of which role the migration runner connects
-- as (default postgres DSN vs. Session-Pooler postgres.<ref>).
UPDATE users
SET sessions_invalid_before = NOW()
WHERE COALESCE(token_version, 0) > 0
  AND sessions_invalid_before IS NULL;

CREATE OR REPLACE FUNCTION public.guard_session_revocation_columns()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS $$
BEGIN
    -- Tamper protection: token_version and sessions_invalid_before are
    -- backend-managed. Only a caller acting within service_role (the backend via
    -- PostgREST; superusers/migration roles are members too) may change them, so
    -- a frontend authenticated/anon user cannot clear or move the watermark via
    -- direct PostgREST and bypass logout-all. Skipped where service_role does
    -- not exist (non-Supabase / CI Postgres) so the migration stays portable.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role')
       AND NOT pg_has_role(current_user, 'service_role', 'MEMBER')
       AND (NEW.token_version IS DISTINCT FROM OLD.token_version
            OR NEW.sessions_invalid_before IS DISTINCT FROM OLD.sessions_invalid_before) THEN
        RAISE EXCEPTION 'token_version and sessions_invalid_before are backend-managed';
    END IF;

    -- Rollout-gap closure: auto-stamp the watermark on any token_version bump
    -- that did not already set it in the same UPDATE. Covers an old backend
    -- instance (or any future code path) that bumps token_version without the
    -- watermark; Firebase sessions are gated only by the watermark.
    IF COALESCE(NEW.token_version, 0) > COALESCE(OLD.token_version, 0)
       AND NEW.sessions_invalid_before IS NOT DISTINCT FROM OLD.sessions_invalid_before THEN
        NEW.sessions_invalid_before := NOW();
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_guard_session_revocation ON public.users;
CREATE TRIGGER trg_guard_session_revocation
    BEFORE UPDATE ON public.users
    FOR EACH ROW
    EXECUTE FUNCTION public.guard_session_revocation_columns();
