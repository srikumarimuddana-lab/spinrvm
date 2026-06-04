-- 110_settings_resend_email.sql
--
-- Migrate transactional email from SendGrid to Resend.
--
-- Spinr is switching its transactional email provider (ride receipts, T4A
-- links, DSAR exports, corporate low-balance + safety alerts) from SendGrid
-- to Resend. Resend uses a different API key format and endpoint, so the
-- credential is a new column rather than a reuse of the old SendGrid key.
--
-- Fields landed:
--   • resend_api_key    — Resend credential (masked on GET via
--     _mask_credentials in routes/admin/settings.py; never returned
--     in plaintext on a settings GET).
--   • resend_from_email — verified sender address, e.g. receipts@spinr.ca.
--     Backfilled from the existing sendgrid_from_email so receipts keep
--     sending from the same verified address with no operator action.
--
-- The old sendgrid_api_key / sendgrid_from_email columns are intentionally
-- left in place (append-only migration policy — never edit/drop a merged
-- migration's columns in flight). The backend no longer reads them; they
-- can be dropped in a later cleanup migration once we've confirmed nothing
-- references them. The Resend API key must be set via the admin Settings
-- page after deploy — it is NOT carried over from SendGrid.
--
-- All columns are nullable so the migration is forward-compatible with
-- running traffic. Blank resend_api_key = email step skipped (same
-- behaviour the SendGrid path had).
--
-- Rollback:
--   ALTER TABLE settings DROP COLUMN IF EXISTS resend_api_key;
--   ALTER TABLE settings DROP COLUMN IF EXISTS resend_from_email;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS resend_api_key    TEXT,
    ADD COLUMN IF NOT EXISTS resend_from_email TEXT;

-- Carry the verified sender address forward so receipts don't silently
-- start sending from the default fallback after the provider switch.
UPDATE public.settings
   SET resend_from_email = sendgrid_from_email
 WHERE id = 'app_settings'
   AND (resend_from_email IS NULL OR resend_from_email = '')
   AND sendgrid_from_email IS NOT NULL
   AND sendgrid_from_email <> '';

COMMENT ON COLUMN public.settings.resend_api_key IS
    'Resend API credential for transactional email (receipts, T4A links, alerts). Masked on GET via _mask_credentials in routes/admin/settings.py; never appears in plaintext in the admin UI. Replaces sendgrid_api_key.';
COMMENT ON COLUMN public.settings.resend_from_email IS
    'Verified Resend sender address (e.g. receipts@spinr.ca). Must be a domain verified in the Resend dashboard. Replaces sendgrid_from_email.';
