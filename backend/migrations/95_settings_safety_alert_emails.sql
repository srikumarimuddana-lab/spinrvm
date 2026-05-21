-- 95_settings_safety_alert_emails.sql
-- Stores the comma-separated list of email addresses that receive a
-- transactional alert whenever a safety_incidents row is opened (rider
-- SOS, driver safety report, or auto-escalation from the safety
-- check-in loop). Edited via the admin Settings page.
--
-- Why a single TEXT column rather than a separate table:
-- ── This is the same single-row app_settings pattern every other
--    global toggle uses (see google_maps_api_key, sendgrid_api_key,
--    company_email). A safety-team distribution list is small (≤5
--    addresses in practice) and never queried by anything other than
--    the safety-alert sender, so a normalised table would add
--    overhead with no payoff.
-- ── Leaving the column blank disables outbound email alerts cleanly;
--    the sender treats empty as "no recipients configured" and just
--    skips the email step. WS broadcast + DB row still happen so
--    nothing else regresses.
--
-- Rollback:
--   ALTER TABLE settings DROP COLUMN IF EXISTS safety_alert_emails;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS safety_alert_emails TEXT;

COMMENT ON COLUMN public.settings.safety_alert_emails IS
    'Comma-separated list of email addresses that get a transactional alert when a safety_incidents row opens. Blank = email step skipped (WS broadcast still fires).';
