-- 188_subscription_payments_stripe_invoice_url.sql
--
-- Add the Stripe-hosted invoice URL to the subscription_payments ledger.
--
-- The Spinr Pass invoice PDF/email template (utils/subscription_invoice_pdf.py)
-- already renders a "View Stripe receipt" link when a stripe_invoice_url is
-- supplied, and the recurring-charge webhook (routes/webhooks.py) already has
-- `invoice.hosted_invoice_url` in hand — but it was never persisted on the
-- payment row, so the admin download / resend (which rebuild the invoice from
-- the stored row) could never include the link. This column persists it so the
-- link survives into later admin downloads and re-sends.
--
-- Nullable: legacy rows and dev/one-off charges (no Stripe invoice) have none;
-- the code writes it only for recurring Stripe charges that carry the URL.
-- Mirrors the additive, nullable pattern of migration 186 (tax columns).
--
-- Forward-compatible: ADD COLUMN IF NOT EXISTS is an instant metadata-only
-- change on Postgres (no table rewrite, no lock on existing rows). Apply this
-- BEFORE deploying the code that writes the column (same expand-first ordering
-- as the 186 tax columns).
--
-- Rollback:
--   ALTER TABLE subscription_payments DROP COLUMN IF EXISTS stripe_invoice_url;

ALTER TABLE subscription_payments
    ADD COLUMN IF NOT EXISTS stripe_invoice_url TEXT;

COMMENT ON COLUMN subscription_payments.stripe_invoice_url IS
    'Stripe hosted_invoice_url for this charge (recurring charges only); rendered as the "View Stripe receipt" link on the Spinr Pass invoice PDF/email. Nullable for legacy/dev/one-off rows.';
