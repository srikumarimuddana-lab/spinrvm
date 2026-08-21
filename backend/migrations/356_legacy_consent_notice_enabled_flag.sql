-- 356: rollout flag for the legacy/organic-user re-consent notice
-- (ACTION_ITEMS.md A41, Oct 30 legacy-migration-playbook checklist item #1).
--
-- Context: backend/routes/legacy_consent.py (GET /consent/status, POST
-- /consent/accept) and both apps' legacy-consent-notice.tsx screen already
-- exist and are fully wired -- otp.tsx, profile-setup.tsx, and index.tsx in
-- both rider-app and driver-app all call GET /consent/status on
-- app-open/login and redirect to /legacy-consent-notice when it reports
-- needs_notice: true. All of that code defensively reads
-- app_settings.legacy_consent_notice_enabled via settings_loader.py's
-- .get("legacy_consent_notice_enabled", False), so it has been running
-- silently as "off" this whole time -- but the column itself was never
-- migrated into the settings table. No file in backend/migrations/ (before
-- this one) references this name; schemas.py's AppSettings model declares
-- the field (Pydantic-side default only), but the live `settings` row has
-- no such column. Same fixed-flat-column failure mode migration 313's own
-- header and migration 353 both call out: a new AppSettings field is not
-- safe to read/write without a matching migration.
--
-- Fixed-flat-column `settings` table (see migration 313/353's own headers)
-- -- additive defaulted column, mirrors driver_discreet_sos_enabled,
-- route_booked_dropoff_anchor_enabled (349), and rideless_sos_enabled (353).
--
-- Unlike 353's flag, this one is NOT a no-op once flipped: both apps are
-- already live-wired to it (see above), so setting this true in any real
-- environment immediately starts showing the notice to every user whose
-- consent_version is behind CONSENT_VERSION. schemas.py's own comment on
-- this field previously claimed otherwise ("flipping this on ahead of that
-- shipping is a no-op") -- that was true when written, went stale once the
-- notice screens shipped, and is corrected in the same commit as this
-- migration. Flipping the value is a separate, deliberate action (a direct
-- DB write, since this field is not yet in routes/admin/settings.py's
-- AdminSettingsUpdate allow-list -- adding it there is a separate
-- follow-up, not part of this migration), not something this migration
-- does.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS legacy_consent_notice_enabled;
--
-- Forward-compatible: additive defaulted column; older backends ignore it.

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS legacy_consent_notice_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.settings.legacy_consent_notice_enabled IS
    'Dark-launch gate for the legacy/organic-user re-consent notice '
    '(ACTION_ITEMS.md A41, GET/POST /consent/*, both apps'' '
    'legacy-consent-notice.tsx). Off = GET /consent/status always reports '
    'needs_notice: false regardless of a user''s actual consent_version, '
    'and POST /consent/accept 404s. On = every user whose consent_version '
    'is behind CONSENT_VERSION (backend/routes/auth.py) is shown the '
    'one-time notice on next app-open/login -- both apps are already live '
    'wired to check this (otp.tsx, profile-setup.tsx, index.tsx), so this '
    'is a real, immediate, user-facing change once flipped, not a no-op. '
    'Still ships false here; enabling in any real environment is a '
    'separate, deliberate action.';
