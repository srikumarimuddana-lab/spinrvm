-- 126_disputes_zoho_ticket.sql
--
-- Rollback:
--   ALTER TABLE disputes DROP COLUMN IF EXISTS zoho_ticket_id;
--
-- Purpose: link a payment dispute / refund request to the Zoho Desk ticket
-- auto-created for it (idempotency + staff cross-reference). Additive column;
-- safe in flight.

ALTER TABLE disputes ADD COLUMN IF NOT EXISTS zoho_ticket_id TEXT;

NOTIFY pgrst, 'reload schema';
