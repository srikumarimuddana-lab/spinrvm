-- 125_lost_and_found_zoho_ticket.sql
--
-- Rollback:
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS zoho_ticket_id;
--
-- Purpose: link a Lost & Found case to the Zoho Desk ticket auto-created for
-- it, so the link is idempotent (we only create a ticket when this is null)
-- and support staff can cross-reference. Additive column; safe in flight.

ALTER TABLE lost_and_found ADD COLUMN IF NOT EXISTS zoho_ticket_id TEXT;

NOTIFY pgrst, 'reload schema';
