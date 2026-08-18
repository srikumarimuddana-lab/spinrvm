-- 326_stripe_disputes_evidence_tracking_columns.sql
--
-- C23 (ACTION_ITEMS.md), Action item 1 of 5: stripe_disputes records a
-- chargeback and nothing else happens. Stripe puts the evidence submission
-- deadline (7-21 days depending on card network) on
-- `dispute.evidence_details.due_by` in every `charge.dispute.created` event
-- payload, and we currently drop it entirely -- miss that date and the
-- dispute is lost automatically with no evidence considered, and today
-- nothing warns as it approaches (that's Action item 2, a separate,
-- follow-up change -- this migration only adds the column the alert will
-- eventually read).
--
-- Adds three columns, all nullable, all additive:
--   evidence_due_by       -- Stripe's evidence_details.due_by, populated by
--                             the charge.dispute.created handler going
--                             forward. NULL for disputes recorded before
--                             this migration (backfill is out of scope --
--                             Stripe's own dashboard remains the source of
--                             truth for any already-open dispute's
--                             deadline; this column only tracks new ones).
--   evidence_submitted_at -- when a support agent (or, later, an automated
--                             path -- Action item 5) actually submitted
--                             evidence. NULL until that happens. Not
--                             populated by this migration or the
--                             create-handler change alone.
--   fee_cents             -- Stripe's per-dispute fee, when known. Not
--                             populated by this migration alone either --
--                             Action items yet to come (or manual entry)
--                             fill this in; the column exists now so a
--                             later, narrower change doesn't need its own
--                             migration.
--
-- Deliberately does NOT touch `amount_cents`, `status`, `reason`, or any
-- existing column/index -- pure column addition, append-only per this
-- repo's migration convention.
--
-- Forward-compatible: three ADD COLUMN IF NOT EXISTS on a table with
-- current row count in the single digits (chargeback volume is ~zero per
-- C23's own note) -- no meaningful lock duration, no CONCURRENTLY needed.
--
-- Rollback: ALTER TABLE stripe_disputes DROP COLUMN IF EXISTS
-- evidence_due_by, DROP COLUMN IF EXISTS evidence_submitted_at,
-- DROP COLUMN IF EXISTS fee_cents; safe at any point -- no other column,
-- function, or view depends on these three.

ALTER TABLE stripe_disputes
    ADD COLUMN IF NOT EXISTS evidence_due_by       timestamptz,
    ADD COLUMN IF NOT EXISTS evidence_submitted_at  timestamptz,
    ADD COLUMN IF NOT EXISTS fee_cents              integer;

COMMENT ON COLUMN stripe_disputes.evidence_due_by IS
    'Stripe dispute.evidence_details.due_by, populated by charge.dispute.created (C23 Action 1). NULL = not yet populated (pre-migration row) or Stripe sent no due_by for this dispute.';

COMMENT ON COLUMN stripe_disputes.evidence_submitted_at IS
    'When evidence was actually submitted for this dispute (manually via Stripe Dashboard today, per docs/runbooks/payment-dispute-evidence.md). NULL until submitted. Not auto-populated by any webhook handler yet (C23 Action 5, not built).';

COMMENT ON COLUMN stripe_disputes.fee_cents IS
    'Stripe per-dispute fee in cents, when known. Not auto-populated by any handler yet -- placeholder for a future C23 action (e.g. reading it from the dispute.balance_transactions the charge.dispute.closed handler already records to financial_events, see B27).';

-- CREATE INDEX IF NOT EXISTS idx_stripe_disputes_evidence_due_by
--     ON stripe_disputes (evidence_due_by)
--     WHERE evidence_due_by IS NOT NULL AND evidence_submitted_at IS NULL;
-- Deferred to the Action-item-2 alerting change (the natural first reader
-- of this index) rather than added speculatively here with no query yet.

NOTIFY pgrst, 'reload schema';
