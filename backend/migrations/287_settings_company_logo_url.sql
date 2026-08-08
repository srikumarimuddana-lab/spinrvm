-- 287_settings_company_logo_url.sql
--
-- Admin-configurable logo for transactional-email headers.
--
-- The email footer's company name, address, support email, website and phone
-- now come from the "Company Info (shown in apps)" card on the admin Settings
-- page (columns added in migrations 87/192 and earlier) rather than from
-- hardcoded constants. The logo was the one piece of that identity with no
-- setting behind it, so it stayed a deploy-time decision while everything
-- beside it became a settings-page decision.
--
-- Empty = use the bundled asset at backend/static/branding/spinr_logo.png,
-- served by routes/branding.py. That is the correct default, not a
-- placeholder: it is the real brand mark, the same file embedded in every
-- Spinr-branded report PDF. Blank is the expected steady state.
--
-- Must be an absolute http(s) URL when set. The value lands in an <img src>
-- inside mail read outside any origin, so a relative path cannot resolve, and
-- utils/company_details._safe_logo_url rejects any non-http(s) scheme and
-- falls back to the bundled asset rather than shipping it.
--
-- Follows the ride_offer_sound_url pattern: an admin-supplied URL for an asset
-- that must be swappable without a redeploy.
--
-- Nullable with no default, so this is forward-compatible with traffic in
-- flight: no table rewrite, existing rows read as NULL, and _coalesce() in
-- utils/company_details treats NULL and "" identically. The backend also
-- defaults it in schemas.AppSettings, so the feature behaves correctly whether
-- or not this migration has run — there is no window where the column's
-- absence breaks a send.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS company_logo_url;
--   (Safe at any time: emails fall back to the bundled asset.)

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS company_logo_url TEXT;

COMMENT ON COLUMN public.settings.company_logo_url IS
    'Absolute http(s) URL of the logo rendered in transactional-email headers (utils/company_details.load_company_details). Empty/NULL falls back to the bundled asset served at /api/v1/branding/spinr-logo.png. Does NOT affect report PDF/Excel/Word headers, which use utils/report_branding.LOGO_PATH.';
