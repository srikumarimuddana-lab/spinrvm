-- 434: revocation generation for the env-credential super admin (admin-001).
--
-- Authored as 433, renumbered before merge: 433 was taken by
-- 433_admin_role_rls_unreachable_phase2_safety_insurance.sql (PR #5597), which
-- landed on main while this branch was in flight. Safe to rename because this
-- file has never been applied anywhere — the runner keys on the full filename,
-- so only ALREADY-APPLIED migrations are frozen (CLAUDE.md, migrations).
--
-- admin-001 is defined by ADMIN_EMAIL/ADMIN_PASSWORD in the environment and has
-- no admin_staff row, so dependencies/__init__.py's _verify_admin_payload
-- skipped is_active, token_version and the idle timeout for it, and
-- /admin/auth/logout-all refused to act on the account at all. The only
-- revocation control was the per-JTI Redis denylist, which fails OPEN on a
-- Redis error by design.
--
-- This column is that account's token_version, with the same semantics as
-- admin_staff.token_version: mint stamps the current value into the token,
-- every admin request compares the claim against it, and logout-all bumps it so
-- every previously-minted token is rejected on its next request.
--
-- Backs backend/utils/env_admin_tokens.py. Read uncached on the request path —
-- the 60s app-settings cache is deliberately bypassed so revocation is not
-- delayed by up to a minute.
--
-- Safe to apply against live traffic: additive, NOT NULL with a DEFAULT, no
-- rewrite of existing rows' meaning, no index, no policy change. Tokens minted
-- before this lands carry token_version 0 and the column defaults to 0, so they
-- keep working until their normal expiry (ADMIN_ACCESS_TOKEN_TTL_HOURS) — the
-- version check is symmetric on 0, exactly like the staff path.
--
-- DEPLOY ORDERING: THIS MIGRATION MUST BE APPLIED BEFORE THE CODE THAT READS
-- IT. utils/env_admin_tokens.py selects this column by name and treats a read
-- failure as fail-closed (503), deliberately: a missing column must never be
-- read as "version 0", because the comparison is `claim < stored` and a stored
-- 0 passes every token ever minted — silently un-revoking everything an
-- operator just killed. The cost of that choice is that deploying the code
-- against a database without this column locks the env super admin out until
-- the migration runs. An earlier draft inverted this (tolerating the missing
-- column by returning 0) and was rejected in review as a fail-open hole in the
-- very control this exists to add.
--
-- Deliberately NOT added to SettingsUpdateRequest: this is auth state an
-- operator changes via /admin/auth/logout-all, not a settings-screen field.
--
-- Rollback (restores the previous behaviour without a redeploy — every token
-- then compares 0 against 0 and passes, which is the pre-433 posture):
--   UPDATE public.settings SET env_admin_token_version = 0 WHERE id = 'app_settings';
-- Schema rollback, after retiring the code readers:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS env_admin_token_version;

SET lock_timeout = '5s';

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS env_admin_token_version INTEGER NOT NULL DEFAULT 0;

RESET lock_timeout;

COMMENT ON COLUMN public.settings.env_admin_token_version IS
    'Revocation generation for the env-credential super admin (admin-001), which has no '
    'admin_staff row. Mint stamps it into the token_version claim; _verify_admin_payload '
    'rejects any admin-001 token whose claim is lower; /admin/auth/logout-all increments it. '
    'Not settable through the admin settings API.';
