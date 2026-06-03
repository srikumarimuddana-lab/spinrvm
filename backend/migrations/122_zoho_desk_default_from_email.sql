-- 122_zoho_desk_default_from_email.sql
--
-- Rollback:
--   ALTER TABLE zoho_desk_config DROP COLUMN IF EXISTS default_from_email;
--
-- Purpose: Zoho Desk's sendReply (EMAIL channel) requires `fromEmailAddress`
-- to be one of the portal's configured support addresses, otherwise the reply
-- is rejected upstream. Store the admin-chosen reply-from address on the
-- single-row config so the backend can attach it to every email reply.
--
-- Backend-only table (RLS already enabled in migration 121); this is a plain
-- additive column, safe against in-flight traffic.

ALTER TABLE zoho_desk_config ADD COLUMN IF NOT EXISTS default_from_email TEXT;

NOTIFY pgrst, 'reload schema';
