-- Editable team-name header for the helpdesk email signature.
-- Defaults to NULL; the renderer falls back to "{company_name} Support".
ALTER TABLE zoho_desk_config
  ADD COLUMN IF NOT EXISTS helpdesk_signature_header TEXT;
