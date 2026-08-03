-- Migration 281: corporate_subscription_plans + corporate_subscriptions
--
-- Rollback:
--   DROP TABLE IF EXISTS public.corporate_subscriptions;
--   DROP TABLE IF EXISTS public.corporate_subscription_plans;
--
-- Flat SaaS subscription billing for corporate accounts (product decision,
-- corporate + admin portal review round 2, "no fee mechanism exists beyond
-- passthrough fares"). Deliberately NOT a per-ride markup/commission —
-- CLAUDE.md's monetization model is "SaaS corporate accounts", never
-- per-trip cuts. A company pays a flat recurring platform fee; ride fares
-- to riders/drivers are completely unaffected by this table.
--
-- corporate_subscription_plans is the admin-managed catalog (a handful of
-- flat tiers). corporate_subscriptions is one row per company's current (or
-- historical) subscription lifecycle, driven by real Stripe Subscription
-- objects — Stripe owns the recurring-charge schedule; this table mirrors
-- its state via the webhook handlers in routes/webhooks.py (same pattern as
-- driver_subscriptions for Spinr Pass, migration 09).
--
-- price on corporate_subscriptions is the price *locked in* at assignment
-- time (copied from the plan) — changing a plan's catalog price later must
-- never retroactively change what an already-subscribed company is billed;
-- that requires an explicit re-assignment.
--
-- status lifecycle:
--   active      subscription is live and Stripe is billing it
--   past_due    most recent invoice failed; Stripe is retrying (dunning)
--   cancelled   ended, either at-period-end or immediately

CREATE TABLE IF NOT EXISTS public.corporate_subscription_plans (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    monthly_price   NUMERIC(10, 2) NOT NULL CHECK (monthly_price >= 0),
    stripe_price_id TEXT,
    description     TEXT DEFAULT '',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS public.corporate_subscriptions (
    id                      TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL REFERENCES public.corporate_accounts(id),
    plan_id                 TEXT REFERENCES public.corporate_subscription_plans(id) ON DELETE SET NULL,
    plan_name               TEXT NOT NULL,
    price                   NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    status                  TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'past_due', 'cancelled')),
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,
    current_period_end      TIMESTAMPTZ,
    cancel_at_period_end    BOOLEAN NOT NULL DEFAULT FALSE,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancelled_at            TIMESTAMPTZ,
    created_by_admin_id     TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ
);

-- At most one active/past_due subscription per company at a time — assigning
-- a new plan must go through cancel-then-create, never a second concurrent
-- row (mirrors the "one active ride at a time" style invariant elsewhere).
CREATE UNIQUE INDEX IF NOT EXISTS idx_corporate_subscriptions_one_live_per_company
    ON public.corporate_subscriptions (company_id)
    WHERE status IN ('active', 'past_due');

-- Admin listing: a company's subscription history.
CREATE INDEX IF NOT EXISTS idx_corporate_subscriptions_company_created
    ON public.corporate_subscriptions (company_id, created_at DESC);

-- Webhook fast-path: match an incoming Stripe event back to our row.
CREATE INDEX IF NOT EXISTS idx_corporate_subscriptions_stripe_sub
    ON public.corporate_subscriptions (stripe_subscription_id);

CREATE INDEX IF NOT EXISTS idx_corporate_subscription_plans_active
    ON public.corporate_subscription_plans (is_active);

-- Service role (backend) bypasses RLS by design; the explicit admin-only
-- policies below match corporate_section_spend's own (migration 282) so a
-- direct authenticated (non-service-role) admin session reads/writes the
-- same rows a service-role-backed route would, rather than silently getting
-- zero rows. Companies never read/write these tables directly — no
-- authenticated-non-admin or anon policy is added.
ALTER TABLE public.corporate_subscription_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.corporate_subscriptions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'corporate_subscription_plans'
          AND policyname = 'Admin full access corporate_subscription_plans'
    ) THEN
        EXECUTE 'CREATE POLICY "Admin full access corporate_subscription_plans" ON corporate_subscription_plans '
                'FOR ALL TO authenticated '
                'USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text AND users.role = ''admin''))';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'corporate_subscriptions'
          AND policyname = 'Admin full access corporate_subscriptions'
    ) THEN
        EXECUTE 'CREATE POLICY "Admin full access corporate_subscriptions" ON corporate_subscriptions '
                'FOR ALL TO authenticated '
                'USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text AND users.role = ''admin''))';
    END IF;
END $$;

COMMENT ON TABLE public.corporate_subscription_plans IS
    'Admin-managed catalog of flat SaaS subscription tiers for corporate accounts.';
COMMENT ON TABLE public.corporate_subscriptions IS
    'One row per company subscription lifecycle; state is mirrored from real Stripe Subscription objects via routes/webhooks.py.';
COMMENT ON COLUMN public.corporate_subscriptions.price IS
    'Price locked in at assignment time (copied from the plan) — a later catalog price change never retroactively rebills an existing subscription.';
COMMENT ON COLUMN public.corporate_subscriptions.status IS
    'active | past_due | cancelled. past_due = most recent Stripe invoice failed, dunning in progress.';
