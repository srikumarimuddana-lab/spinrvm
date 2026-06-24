-- 192_settings_business_identity.sql
--
-- Configurable business identity for the CASL-required footer on marketing
-- messages, plus a dedicated marketing sender address.
--
-- CASL requires every commercial electronic message to identify the sender and
-- include a valid physical mailing address. We already have company_name and
-- company_address (migration that added the public "business card" fields,
-- exposed via GET /api/company-info). This migration completes the postal
-- address so the footer can render a full, legally-valid address that operators
-- edit from admin Settings instead of it being hard-coded anywhere:
--
--   • company_city            — municipality (e.g. Saskatoon).
--   • company_province         — province/territory (e.g. SK).
--   • company_postal_code      — Canadian postal code.
--
-- And a separate marketing sender so promotional mail goes from a distinct
-- identity (e.g. news@spinr.ca) rather than the transactional receipts address,
-- which protects the transactional sender's reputation from marketing
-- complaints:
--
--   • marketing_from_email     — verified SES sender for marketing email.
--     Falls back to aws_ses_from_email when blank.
--
-- None of these are credentials — they are the same public info as the existing
-- company_* fields, so they are NOT masked on the admin settings GET.
--
-- All columns nullable → forward-compatible with running traffic.
--
-- RLS: the settings table is service-role-only (backend) — no user-facing RLS
-- policy is added or required here.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS company_city;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS company_province;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS company_postal_code;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS marketing_from_email;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS company_city         TEXT,
    ADD COLUMN IF NOT EXISTS company_province     TEXT,
    ADD COLUMN IF NOT EXISTS company_postal_code  TEXT,
    ADD COLUMN IF NOT EXISTS marketing_from_email TEXT;

COMMENT ON COLUMN public.settings.company_city IS
    'Business municipality for the CASL marketing footer (e.g. Saskatoon). Public info; not masked.';
COMMENT ON COLUMN public.settings.company_province IS
    'Business province/territory for the CASL marketing footer (e.g. SK). Public info; not masked.';
COMMENT ON COLUMN public.settings.company_postal_code IS
    'Business postal code for the CASL marketing footer. Public info; not masked.';
COMMENT ON COLUMN public.settings.marketing_from_email IS
    'Verified SES sender for MARKETING email (e.g. news@spinr.ca). Separate from aws_ses_from_email so marketing complaints do not harm the transactional sender reputation. Falls back to aws_ses_from_email when blank.';
