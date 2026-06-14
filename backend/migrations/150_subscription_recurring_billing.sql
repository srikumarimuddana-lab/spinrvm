-- Migration 150: Recurring-billing fields for Spinr Pass (B3)
--
-- Enables true Stripe-Billing auto-renew alongside the existing one-off
-- Checkout-per-period flow:
--   • subscription_plans.stripe_price_id — the recurring Price created in
--     the Stripe dashboard for this plan. When set, subscribe_to_plan uses
--     mode="subscription" Checkout (auto-renew); when null it falls back to
--     the one-off mode="payment" Checkout. Plans are therefore opt-in to
--     recurring with zero change to existing one-off plans.
--   • driver_subscriptions.stripe_subscription_id — the Stripe Subscription
--     (sub_...) id, captured from checkout.session.completed and used to
--     match invoice.paid / invoice.payment_failed / customer.subscription.*
--     webhook events back to the driver's row.
--
-- stripe_subscription_id may already exist from the legacy 08_complete_schema
-- definition of driver_subscriptions; IF NOT EXISTS makes this idempotent.
--
-- Forward-compatible: both columns nullable, no backfill. Absent values read
-- as NULL — existing one-off subscriptions and plans are unaffected.
--
-- Rollback:
--   ALTER TABLE subscription_plans   DROP COLUMN IF EXISTS stripe_price_id;
--   ALTER TABLE driver_subscriptions DROP COLUMN IF EXISTS stripe_subscription_id;

ALTER TABLE public.subscription_plans
    ADD COLUMN IF NOT EXISTS stripe_price_id TEXT;

ALTER TABLE public.driver_subscriptions
    ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;

-- Renewal/cancellation webhooks look up the row by stripe_subscription_id.
CREATE INDEX IF NOT EXISTS idx_driver_subscriptions_stripe_subscription
    ON public.driver_subscriptions(stripe_subscription_id);

COMMENT ON COLUMN public.subscription_plans.stripe_price_id IS
    'Recurring Stripe Price (price_...) for auto-renew billing. When set, '
    'subscribe_to_plan uses mode=subscription Checkout; null = one-off '
    'Checkout-per-period. Create the Price in the Stripe dashboard.';

COMMENT ON COLUMN public.driver_subscriptions.stripe_subscription_id IS
    'Stripe Subscription id (sub_...) for recurring plans. Captured from '
    'checkout.session.completed; matches invoice.paid / invoice.payment_failed '
    '/ customer.subscription.* events back to this row.';
