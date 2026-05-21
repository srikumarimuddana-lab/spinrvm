-- 93_settings_missing_columns.sql
--
-- The admin settings page exposes a handful of fields the Pydantic schema
-- accepts but the `settings` table never had columns for. Saving any of
-- them surfaced as `PGRST204: Could not find the '<col>' column of
-- 'settings' in the schema cache`. Adding them all in one migration so
-- the admin Settings page is fully savable end-to-end.
--
-- Fields landed:
--   • Company info (rider receipts, T4A slips, dashboard footer):
--     company_name, company_address, company_phone, company_email,
--     company_website
--   • SendGrid integration (transactional email — receipts, T4A links):
--     sendgrid_api_key, sendgrid_from_email
--   • Twilio Proxy (masks rider↔driver phone numbers during a ride):
--     twilio_proxy_service_sid
--   • Fare-lock toggle (quoted-at-booking guarantees, prevents mid-trip
--     fare drift if Maps changes the route):
--     fare_lock_enabled
--   • Subscription gate (require driver Spinr Pass to go online):
--     require_driver_subscription
--   • Share-trip live-tracking base URL (rider-app safety contact links):
--     track_base_url
--
-- All columns are nullable / default-empty so the migration is
-- forward-compatible with running traffic. No backfill needed —
-- absent values read as NULL on existing rows, which the Pydantic
-- model handles via Optional + the GET endpoint's masking/dump pass.
--
-- Rollback:
--   ALTER TABLE settings DROP COLUMN IF EXISTS company_name;
--   ALTER TABLE settings DROP COLUMN IF EXISTS company_address;
--   ALTER TABLE settings DROP COLUMN IF EXISTS company_phone;
--   ALTER TABLE settings DROP COLUMN IF EXISTS company_email;
--   ALTER TABLE settings DROP COLUMN IF EXISTS company_website;
--   ALTER TABLE settings DROP COLUMN IF EXISTS sendgrid_api_key;
--   ALTER TABLE settings DROP COLUMN IF EXISTS sendgrid_from_email;
--   ALTER TABLE settings DROP COLUMN IF EXISTS twilio_proxy_service_sid;
--   ALTER TABLE settings DROP COLUMN IF EXISTS fare_lock_enabled;
--   ALTER TABLE settings DROP COLUMN IF EXISTS require_driver_subscription;
--   ALTER TABLE settings DROP COLUMN IF EXISTS track_base_url;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS company_name                TEXT,
    ADD COLUMN IF NOT EXISTS company_address             TEXT,
    ADD COLUMN IF NOT EXISTS company_phone               TEXT,
    ADD COLUMN IF NOT EXISTS company_email               TEXT,
    ADD COLUMN IF NOT EXISTS company_website             TEXT,
    ADD COLUMN IF NOT EXISTS sendgrid_api_key            TEXT,
    ADD COLUMN IF NOT EXISTS sendgrid_from_email         TEXT,
    ADD COLUMN IF NOT EXISTS twilio_proxy_service_sid    TEXT,
    ADD COLUMN IF NOT EXISTS fare_lock_enabled           BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS require_driver_subscription BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS track_base_url              TEXT;

COMMENT ON COLUMN public.settings.company_address IS
    'Legal address of the operating company. Shown on rider receipts, driver T4A slips, and the admin dashboard footer.';
COMMENT ON COLUMN public.settings.sendgrid_api_key IS
    'SendGrid credential. Masked on GET via _mask_credentials in routes/admin/settings.py; never appears in plaintext in the admin UI.';
COMMENT ON COLUMN public.settings.twilio_proxy_service_sid IS
    'Twilio Proxy service ID used to mask rider↔driver phone numbers during an active trip. Lives in app_settings so it rotates without a redeploy.';
COMMENT ON COLUMN public.settings.fare_lock_enabled IS
    'When true, the rider-quoted fare at booking time is locked even if Maps changes the route mid-trip. False = fare follows actual distance/duration.';
COMMENT ON COLUMN public.settings.track_base_url IS
    'Public base URL for the rider-app Share Trip page. Should point at the deployed admin dashboard /track route, e.g. https://admin.example.com/track. Recipients open ${base}/${share_token}.';
