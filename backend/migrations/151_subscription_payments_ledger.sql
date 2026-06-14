-- Migration 151: subscription_payments ledger
--
-- Records each realized Spinr Pass payment as its own row so admin revenue and
-- transaction stats capture recurring renewals (not just the first charge) and
-- stay accurate independent of driver_subscriptions row mutations
-- (cancel/supersede/expire).
--
-- Write points (backend):
--   - invoice.paid webhook       → billing_reason subscription_create / subscription_cycle
--   - _activate_subscription     → billing_reason one_off  (one-off Checkout activation)
--   - subscribe_to_plan dev path → billing_reason dev      (no-Stripe instant activation)
-- Recurring rows dedupe on stripe_invoice_id (one ledger row per Stripe invoice).
--
-- The backfill seeds one 'legacy' row per pre-existing realized subscription so
-- historical revenue isn't lost when stats switch to reading this table.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.subscription_payments;

CREATE TABLE IF NOT EXISTS public.subscription_payments (
    id                       TEXT PRIMARY KEY,
    driver_id                TEXT NOT NULL,
    subscription_id          TEXT,
    plan_id                  TEXT,
    plan_name                TEXT,
    amount                   NUMERIC(10, 2) NOT NULL DEFAULT 0,
    currency                 TEXT NOT NULL DEFAULT 'cad',
    -- subscription_create | subscription_cycle | one_off | dev | legacy
    billing_reason           TEXT NOT NULL DEFAULT 'one_off',
    stripe_invoice_id        TEXT,
    stripe_session_id        TEXT,
    stripe_payment_intent_id TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One ledger row per Stripe invoice — makes recurring renewal inserts idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_payments_invoice
    ON public.subscription_payments (stripe_invoice_id)
    WHERE stripe_invoice_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_subscription_payments_driver
    ON public.subscription_payments (driver_id);
CREATE INDEX IF NOT EXISTS idx_subscription_payments_created
    ON public.subscription_payments (created_at);

ALTER TABLE public.subscription_payments ENABLE ROW LEVEL SECURITY;

-- Driver can read their own payment history; backend (service role) bypasses RLS.
DROP POLICY IF EXISTS subscription_payments_select_own ON public.subscription_payments;
CREATE POLICY subscription_payments_select_own ON public.subscription_payments
    FOR SELECT TO authenticated
    USING (
        driver_id IN (SELECT id FROM public.drivers WHERE user_id = auth.uid())
    );

-- Backfill realized revenue from existing subscriptions (idempotent via id).
-- Excludes never-paid checkout rows (pending/superseded). Runs once (the runner
-- keys on filename); ON CONFLICT guards against any accidental re-run.
INSERT INTO public.subscription_payments
    (id, driver_id, subscription_id, plan_id, plan_name, amount, currency, billing_reason, created_at)
SELECT
    'legacy_' || ds.id,
    ds.driver_id,
    ds.id,
    ds.plan_id,
    ds.plan_name,
    COALESCE(ds.price, 0),
    'cad',
    'legacy',
    COALESCE(ds.started_at, ds.created_at, NOW())
FROM public.driver_subscriptions ds
WHERE ds.status NOT IN ('pending', 'superseded')
  AND COALESCE(ds.price, 0) > 0
ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE public.subscription_payments IS
    'Append-only ledger of realized Spinr Pass payments. Source of truth for '
    'admin subscription revenue/transaction stats (driver_subscriptions tracks '
    'current subscription STATE; this tracks money MOVED).';
