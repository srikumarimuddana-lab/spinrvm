-- Migration 96: payouts — instant-payout reversal & failure-reason columns
--
-- Why:
--   Instant payouts are a two-step Stripe operation: Transfer (platform →
--   connect account), then Payout(method=instant) on the connect account.
--   If the second step fails after the first has succeeded, the money is
--   stranded in the driver's connect account with no DB record — a manual
--   retry creates a second transfer and a duplicate payout.
--
--   The fix in routes/drivers.py now (1) persists the payout row with the
--   transfer id BEFORE attempting the payout step, (2) attempts a Stripe
--   Transfer.create_reversal() when the payout step fails. This migration
--   adds the columns those two steps need:
--
--   stripe_transfer_id      — Stripe Transfer ID from step 1, stored so a
--                             follow-up reversal call can target it
--                             without re-querying Stripe.
--   failure_reason          — first 500 chars of the underlying Stripe
--                             error so admin/support can triage without
--                             cross-referencing log timestamps.
--   requires_manual_review  — true when payout failed AND reversal also
--                             failed; ops dashboard filters on this so
--                             stranded payouts don't fall through.
--
--   The existing standard-payout flow doesn't write these columns, so old
--   rows stay valid. New "stranded"/"reversed" statuses join the existing
--   pending/completed/failed/cancelled set.
--
-- Rollback plan:
--   ALTER TABLE payouts
--       DROP COLUMN IF EXISTS stripe_transfer_id,
--       DROP COLUMN IF EXISTS failure_reason,
--       DROP COLUMN IF EXISTS requires_manual_review;

ALTER TABLE payouts
    ADD COLUMN IF NOT EXISTS stripe_transfer_id     TEXT,
    ADD COLUMN IF NOT EXISTS failure_reason         TEXT,
    ADD COLUMN IF NOT EXISTS requires_manual_review BOOLEAN DEFAULT FALSE;

-- Partial index supports the "needs ops attention" admin query without
-- scanning the full payouts table.
CREATE INDEX IF NOT EXISTS idx_payouts_manual_review
    ON payouts (created_at DESC)
    WHERE requires_manual_review = TRUE;
