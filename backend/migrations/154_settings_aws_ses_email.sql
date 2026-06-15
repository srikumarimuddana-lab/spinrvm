-- 154_settings_aws_ses_email.sql
--
-- Add AWS SES as the PRIMARY transactional-email provider, with Resend kept
-- as a guardrail fallback.
--
-- Rationale: SES is materially cheaper than Resend at our send volume
-- (ride receipts, T4A links, DSAR exports, corporate low-balance + safety
-- alerts). We make SES primary and fall back to the existing Resend path
-- when SES is unconfigured OR a send fails, so a SES outage/mis-config never
-- silently drops a receipt.
--
-- Fields landed:
--   • aws_ses_region            — SES region. Defaults to Canada (ca-central-1)
--     for PIPEDA data-residency alignment; operator may override.
--   • aws_ses_access_key_id      — IAM access key id (not masked on GET; it is
--     an identifier, unusable without the secret — same treatment as
--     twilio_account_sid).
--   • aws_ses_secret_access_key  — IAM secret (CREDENTIAL: masked on GET via
--     _mask_credentials in routes/admin/settings.py; never returned in
--     plaintext on a settings GET).
--   • aws_ses_from_email         — SES verified sender address, e.g.
--     receipts@spinr.ca. Must be a verified identity in the SES account/
--     domain. Falls back to resend_from_email / a code default when blank.
--
-- The Resend columns (migration 110) are intentionally left untouched — they
-- remain the fallback provider. Nothing is dropped here.
--
-- All columns are nullable so the migration is forward-compatible with
-- running traffic. Blank aws_ses_secret_access_key (or blank access key id)
-- = SES considered unconfigured, and email routes straight to the Resend
-- guardrail (same behaviour as before this migration).
--
-- RLS: the settings table is service-role-only (backend) — no user-facing
-- RLS policy is added or required here.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS aws_ses_region;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS aws_ses_access_key_id;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS aws_ses_secret_access_key;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS aws_ses_from_email;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS aws_ses_region            TEXT DEFAULT 'ca-central-1',
    ADD COLUMN IF NOT EXISTS aws_ses_access_key_id     TEXT,
    ADD COLUMN IF NOT EXISTS aws_ses_secret_access_key TEXT,
    ADD COLUMN IF NOT EXISTS aws_ses_from_email        TEXT;

-- Carry the verified Resend sender forward as the SES default so operators
-- only have to verify the same address in SES — keeps receipts sending from
-- the same address once SES credentials are entered.
UPDATE public.settings
   SET aws_ses_from_email = resend_from_email
 WHERE id = 'app_settings'
   AND (aws_ses_from_email IS NULL OR aws_ses_from_email = '')
   AND resend_from_email IS NOT NULL
   AND resend_from_email <> '';

COMMENT ON COLUMN public.settings.aws_ses_region IS
    'AWS SES region for transactional email. Defaults to ca-central-1 (Canada) for PIPEDA data residency.';
COMMENT ON COLUMN public.settings.aws_ses_access_key_id IS
    'AWS IAM access key id for SES SendEmail. Not masked on GET (identifier only; unusable without the secret).';
COMMENT ON COLUMN public.settings.aws_ses_secret_access_key IS
    'AWS IAM secret access key for SES. CREDENTIAL — masked on GET via _mask_credentials in routes/admin/settings.py; never returned in plaintext.';
COMMENT ON COLUMN public.settings.aws_ses_from_email IS
    'Verified SES sender address (e.g. receipts@spinr.ca). Must be a verified identity in the SES account. Falls back to resend_from_email when blank.';
