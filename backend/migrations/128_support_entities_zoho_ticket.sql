-- 128_support_entities_zoho_ticket.sql
--
-- Rollback:
--   ALTER TABLE complaints       DROP COLUMN IF EXISTS zoho_ticket_id;
--   ALTER TABLE flags            DROP COLUMN IF EXISTS zoho_ticket_id;
--   ALTER TABLE safety_incidents DROP COLUMN IF EXISTS zoho_ticket_id;
--
-- Purpose: link complaints, flags, and safety incidents to the Zoho Desk
-- ticket auto-created for each (idempotency + reverse-close: when the Zoho
-- ticket is closed, the sync closes the linked record). lost_and_found (125)
-- and disputes (126) already have this column.
--
-- Additive columns; safe in flight.

ALTER TABLE complaints       ADD COLUMN IF NOT EXISTS zoho_ticket_id TEXT;
ALTER TABLE flags            ADD COLUMN IF NOT EXISTS zoho_ticket_id TEXT;
ALTER TABLE safety_incidents ADD COLUMN IF NOT EXISTS zoho_ticket_id TEXT;

-- Reverse-close looks records up by their linked Zoho ticket id.
CREATE INDEX IF NOT EXISTS idx_complaints_zoho       ON complaints (zoho_ticket_id);
CREATE INDEX IF NOT EXISTS idx_flags_zoho            ON flags (zoho_ticket_id);
CREATE INDEX IF NOT EXISTS idx_safety_zoho           ON safety_incidents (zoho_ticket_id);
CREATE INDEX IF NOT EXISTS idx_lost_and_found_zoho   ON lost_and_found (zoho_ticket_id);
CREATE INDEX IF NOT EXISTS idx_disputes_zoho         ON disputes (zoho_ticket_id);

NOTIFY pgrst, 'reload schema';
