-- 408_rides_refund_amount.sql
--
-- Formalizes rides.refund_amount as a tracked migration. ACTION_ITEMS.md C88
-- found that backend/routes/webhooks.py's charge.refunded handler has read
-- and written this column since before this session (the F1 compare-and-swap
-- hardening only added a filter on top of an already-existing column
-- reference) -- yet no migration file anywhere in this repo ever created it.
-- Wherever it lives on production, it was added by untracked, ad-hoc DDL
-- outside the migration system: exactly the class of drift the append-only
-- migration convention exists to prevent, just predating full coverage of
-- that convention.
--
-- IF NOT EXISTS makes this safe to run anywhere: a no-op on an environment
-- (production) that already has the column via that ad-hoc DDL, and the
-- actual column-creating migration on any environment that doesn't (found
-- missing entirely on the spinrmobileapp project during C88's investigation,
-- and on any future fresh bootstrap from backend/migrations/ alone).
--
-- NUMERIC(8,2) matches disputes.refund_amount (migration 10) -- the only
-- other refund_amount column in this schema -- and the dollar-amount (not
-- cents) semantics webhooks.py's dollars_to_cents()/str() round-trip already
-- assumes. NUMERIC(8,2) caps at $999,999.99, comfortably above any
-- plausible single-ride refund.
--
-- No index: the only additional-column read is webhooks.py's CAS
-- update_one({"id": ride_id, "refund_amount": prev_refund_amount_raw}, ...),
-- already scoped by primary key id -- refund_amount there is a secondary
-- equality guard on an already-narrowed single row, not a scan predicate.
--
-- Rollback: ALTER TABLE rides DROP COLUMN refund_amount;
-- (destructive on any environment where charge.refunded has already written
-- a real value -- confirm no in-flight refund CAS check depends on it before
-- dropping)

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS refund_amount NUMERIC(8,2) DEFAULT 0;

COMMENT ON COLUMN rides.refund_amount IS
    'Cumulative Stripe refund amount for this ride''s charge, in dollars. '
    'Read/written by the charge.refunded webhook handler (routes/webhooks.py) '
    'as the compare-and-swap key preventing duplicate/out-of-order refund '
    'ledger entries -- see ACTION_ITEMS.md C88 and '
    'docs/change-log/2026-09-08-charge-refunded-cas-ledger-dedupe.md.';

NOTIFY pgrst, 'reload schema';
