-- 327_stripe_disputes_evidence_reminder_claim_flag.sql
--
-- C23 (ACTION_ITEMS.md), Action item 2 of 5 (half of it): the T-3-days
-- evidence-deadline reminder loop (utils/dispute_evidence_reminder.py)
-- needs a claim-flag column so the alert fires exactly once per dispute,
-- not once per replica per tick. Same idempotency shape as
-- driver_subscriptions.expiry_warned_3d (migration 178) -- an atomic
-- `UPDATE ... WHERE evidence_reminder_sent_at IS NULL` claim, so only the
-- replica whose write actually matches a row proceeds to alert.
--
-- Additive, nullable, single column. No index added: query volume on
-- stripe_disputes is currently ~zero (per C23's own note) and the loop's
-- own WHERE clause already filters primarily on evidence_due_by (which
-- migration 326 also left unindexed for the same reason) -- revisit both
-- together if/when real chargeback volume justifies it.
--
-- Rollback: ALTER TABLE stripe_disputes DROP COLUMN IF EXISTS
-- evidence_reminder_sent_at; safe at any point -- no other column,
-- function, or view depends on it. The loop itself degrades gracefully if
-- rolled back (get_rows on a nonexistent column would error, so the
-- background loop's own top-level try/except logs and waits for the next
-- tick rather than crashing -- but do redeploy backend code that no longer
-- references this column before/alongside the rollback to avoid that noisy
-- state).

ALTER TABLE stripe_disputes
    ADD COLUMN IF NOT EXISTS evidence_reminder_sent_at timestamptz;

COMMENT ON COLUMN stripe_disputes.evidence_reminder_sent_at IS
    'Claim-flag: set once utils/dispute_evidence_reminder.py has alerted on this dispute''s approaching evidence deadline (C23 Action 2). NULL = not yet alerted. Same atomic-claim idempotency shape as driver_subscriptions.expiry_warned_3d (migration 178).';

NOTIFY pgrst, 'reload schema';
