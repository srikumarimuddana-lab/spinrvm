-- Add a configurable email signature for helpdesk replies.
-- Stored as HTML; appended to every outbound email reply by the backend
-- before forwarding to Zoho Desk's sendReply API.
ALTER TABLE zoho_desk_config
  ADD COLUMN IF NOT EXISTS helpdesk_email_signature TEXT;
