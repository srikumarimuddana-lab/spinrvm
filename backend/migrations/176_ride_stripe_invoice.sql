-- Migration 176: ride payable-invoice fields for the admin "Send Invoice" flow
--
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS idx_rides_stripe_invoice_id;
--   ALTER TABLE rides DROP COLUMN IF EXISTS stripe_invoice_id;
--   ALTER TABLE rides DROP COLUMN IF EXISTS invoice_url;
--   ALTER TABLE rides DROP COLUMN IF EXISTS invoice_sent_at;
--
-- Why: when a rider's card is rejected at the end of a trip and they cannot
-- pay in-app, an admin can send a real Stripe Invoice to the rider's email
-- (collection_method=send_invoice). The rider pays the hosted invoice and the
-- invoice.paid webhook flips the ride to payment_status='paid' and credits the
-- driver via the financial_events ledger — no manual "mark paid"/waive.
--
-- These columns are backend-written only (the service role bypasses RLS); they
-- carry no new user-writable surface, so the existing rides RLS policies cover
-- them and no new policy is required. The add is forward-compatible with
-- in-flight traffic: all three are nullable with no default backfill.
--
-- The index supports the webhook's lookup of a ride by the Stripe invoice id
-- (the webhook receives the invoice, not the ride id, when metadata is absent)
-- and the reconciliation join; it is partial so it only covers invoiced rides.
-- It is built CONCURRENTLY (no SHARE lock on the hot rides table). The runner
-- (backend/scripts/migrate.py) detects "CONCURRENTLY" and applies the whole
-- file in autocommit, executing each statement individually — same pattern as
-- migration 156.

ALTER TABLE rides ADD COLUMN IF NOT EXISTS stripe_invoice_id TEXT;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS invoice_url TEXT;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS invoice_sent_at TIMESTAMPTZ;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_stripe_invoice_id
    ON rides (stripe_invoice_id)
    WHERE stripe_invoice_id IS NOT NULL;
