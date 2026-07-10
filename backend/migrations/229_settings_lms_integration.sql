-- 229_settings_lms_integration.sql
--
-- Add Spinr driver LMS (training platform) integration credentials to the
-- app settings row, editable from the admin dashboard Settings → Integrations
-- tab. Used by backend/services/lms_service.py to pull a driver's training
-- record (registration, completion %, certificates, history) into the driver
-- details drawer, matched by phone number.
--
-- Fields landed:
--   • lms_api_base_url — base URL of the LMS deployment
--     (e.g. https://training.spinr.ca).
--   • lms_api_key      — shared secret sent as the x-api-key header; must
--     equal SPINR_INTEGRATION_API_KEY on the LMS side. CREDENTIAL: masked on
--     GET via _mask_credentials in routes/admin/settings.py.
--
-- Both columns are nullable so the migration is forward-compatible with
-- running traffic. Blank values = integration unconfigured; the admin
-- training endpoint returns 503 and the dashboard reports "not configured".
--
-- RLS: the settings table is service-role-only (backend) — no user-facing
-- RLS policy is added or required here.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS lms_api_base_url;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS lms_api_key;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS lms_api_base_url TEXT,
    ADD COLUMN IF NOT EXISTS lms_api_key      TEXT;

COMMENT ON COLUMN public.settings.lms_api_base_url IS
    'Base URL of the Spinr driver training LMS (e.g. https://training.spinr.ca). Blank = LMS integration disabled.';
COMMENT ON COLUMN public.settings.lms_api_key IS
    'Shared secret for the LMS integration API (x-api-key header; equals SPINR_INTEGRATION_API_KEY on the LMS). CREDENTIAL — masked on GET via _mask_credentials; never returned in plaintext.';
