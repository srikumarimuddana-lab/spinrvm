-- 419: per-company pilot gate for corporate SaaS subscription billing.
--
-- Context: migration 281 added corporate_subscription_plans/corporate_subscriptions
-- and routes/corporate_subscriptions.py gates POST /admin/corporate-accounts/{id}/
-- subscription behind a single global settings flag
-- (corporate_subscription_billing_enabled, migration 313, default false). That
-- flag is all-or-nothing: once flipped on, an admin can start a real Stripe
-- subscription for ANY corporate account, not just a chosen pilot. There is no
-- confirmed sales pipeline yet (ACTION_ITEMS.md G7) and no staging environment
-- (docs/runbooks/staging-environment.md is scaffolding only) to verify the flow
-- against a real Stripe account before opening it to every company.
--
-- This migration adds a second, narrower gate: a per-company boolean so billing
-- can be verified against one company (starting with Spinr's own internal
-- account, exercised via mocked Stripe in tests first, per this change's
-- Change Impact Log) without exposing the assign-subscription endpoint for
-- every other corporate account the moment the global flag is turned on.
-- routes/corporate_subscriptions.py's assign_company_subscription will require
-- BOTH corporate_subscription_billing_enabled (global) AND this column (this
-- company) before calling assign_subscription. Cancelling a subscription stays
-- ungated by either flag, matching the existing "cancel must never be gated"
-- rule in corporate_subscriptions.py.
--
-- Default false: additive, changes no live behavior on its own. No RLS policy
-- change needed -- migration 416's "Admin read corporate_accounts" policy is
-- row-level, already covers this new column, and all backend reads/writes of
-- corporate_accounts go through the service-role client (bypasses RLS) per
-- migration 416's own Change Impact Log grep.
--
-- Rollback:
--   ALTER TABLE public.corporate_accounts DROP COLUMN IF EXISTS subscription_billing_pilot_enabled;
--
-- Forward-compatible: additive defaulted column; older backends ignore it.

ALTER TABLE public.corporate_accounts
    ADD COLUMN IF NOT EXISTS subscription_billing_pilot_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.corporate_accounts.subscription_billing_pilot_enabled IS
    'Per-company pilot gate for flat SaaS subscription billing (routes/corporate_subscriptions.py). '
    'Off (default) = POST .../subscription is refused for this company even if the global '
    'corporate_subscription_billing_enabled setting is on. Both this column and the global '
    'setting must be true for an admin to start a real Stripe subscription for this company. '
    'Never gates subscription cancellation.';
