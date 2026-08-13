-- Toggle for the auto-generated helpdesk email signature.
-- When TRUE the backend builds the signature at send time from
-- app_settings (company name, logo, support email, address).
-- The existing helpdesk_email_signature column doubles as an
-- optional custom tagline shown under the team name.
ALTER TABLE zoho_desk_config
  ADD COLUMN IF NOT EXISTS helpdesk_signature_enabled BOOLEAN DEFAULT FALSE;
