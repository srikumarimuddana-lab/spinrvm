-- 377_zoho_desk_tickets_sla_breach_alerted.sql
--
-- Rollback:
--   ALTER TABLE zoho_desk_tickets DROP COLUMN IF EXISTS sla_breach_alerted_at;
--
-- Purpose: idempotency flag for the new support-ticket SLA-breach sweep loop
-- (utils/support_sla.py, ACTION_ITEMS.md G8). Additive/nullable — does not
-- change any existing read of zoho_desk_tickets. The loop atomically claims a
-- breached P1 ticket by UPDATE ... WHERE sla_breach_alerted_at IS NULL
-- RETURNING zoho_id, so every replica can run the sweep concurrently without
-- double-counting the spinr_support_ticket_sla_breach_total metric or
-- double-logging the same breach.
--
-- Forward-compatible & idempotent; safe against live traffic (single nullable
-- column add, no backfill, no lock beyond the brief ALTER TABLE metadata lock).

ALTER TABLE zoho_desk_tickets ADD COLUMN IF NOT EXISTS sla_breach_alerted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_zdt_sla_breach_pending
    ON zoho_desk_tickets (priority, created_time)
    WHERE sla_breach_alerted_at IS NULL;

NOTIFY pgrst, 'reload schema';
