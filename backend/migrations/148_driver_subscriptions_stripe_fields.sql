-- Migration 148: Add Stripe checkout session fields to driver_subscriptions
--
-- The driver_subscriptions table had conflicting definitions across migrations.
-- This migration consolidates the schema to support Stripe Checkout Session payment flow:
-- - stripe_session_id: Stripe Checkout Session ID, used for verify-session polling
-- - stripe_charge_id: legacy, kept for off-session fallback auditing
-- - payment_status: "pending" | "paid" | "failed" | "requires_action"
-- - expiry_warned: tracks if the driver was notified of upcoming expiry
--
-- Rollback: These columns can be dropped if the Checkout flow is reverted,
-- but the feature remains functional for any active subscriptions (these
-- columns are optional side-effects).

ALTER TABLE IF EXISTS public.driver_subscriptions
    ADD COLUMN IF NOT EXISTS stripe_session_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_charge_id TEXT,
    ADD COLUMN IF NOT EXISTS payment_status TEXT DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS expiry_warned BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS plan_name TEXT,
    ADD COLUMN IF NOT EXISTS price NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS rides_per_day INTEGER DEFAULT -1,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

-- Index for webhook lookup by stripe_session_id
CREATE INDEX IF NOT EXISTS idx_driver_subscriptions_stripe_session
    ON public.driver_subscriptions(stripe_session_id);

-- Index for expiry enforcement lookup
CREATE INDEX IF NOT EXISTS idx_driver_subscriptions_expires_at
    ON public.driver_subscriptions(expires_at, status)
    WHERE status = 'active' AND expires_at IS NOT NULL;

COMMENT ON COLUMN public.driver_subscriptions.stripe_session_id IS
    'Stripe Checkout Session ID for the payment. Used by verify-session endpoint to poll payment status.';

COMMENT ON COLUMN public.driver_subscriptions.payment_status IS
    '"pending" until checkout.session.completed webhook fires, then "paid" (or "failed" if payment failed).';

COMMENT ON COLUMN public.driver_subscriptions.expiry_warned IS
    'True if driver was notified of expiry within 24h. Prevents duplicate notifications.';

COMMENT ON COLUMN public.driver_subscriptions.expires_at IS
    'Subscription expiry timestamp. Used by check_expiring_subscriptions() background loop.';
