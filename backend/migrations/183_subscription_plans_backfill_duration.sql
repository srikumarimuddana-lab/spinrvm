-- Migration 183: Backfill duration_days from billing_period on subscription_plans
--
-- Migration 181 added duration_days with DEFAULT 30, which PostgreSQL applied to
-- every existing row. Any plan that had billing_period='daily' or 'weekly' now
-- has an incorrect duration_days=30, making checkout compute the wrong expiry and
-- failing Stripe Price interval validation.
--
-- This migration corrects existing rows whose duration_days is still the migration-
-- set default (30) but whose billing_period implies a different interval. It is
-- intentionally narrow: rows that were explicitly set to 30 days via the new admin
-- UI (after migration 181) are left alone because they already carry the right value.
--
-- Rollback: no-op (values are already stored; re-run with inverted CASE if needed).

UPDATE public.subscription_plans
SET duration_days = CASE billing_period
    WHEN 'daily'   THEN 1
    WHEN 'weekly'  THEN 7
    WHEN 'monthly' THEN 30
    WHEN 'yearly'  THEN 365
    ELSE 30
END
WHERE
    -- billing_period column exists only on tables created by migration 08;
    -- migration 09 tables never had billing_period so this WHERE is a no-op there.
    billing_period IS NOT NULL
    AND billing_period <> 'monthly'
    AND duration_days = 30;  -- only fix rows still carrying the migration-set default

COMMENT ON COLUMN public.subscription_plans.duration_days IS
    'Plan length in days: 1=daily, 7=weekly, 30=monthly, 365=yearly. '
    'Backfilled from billing_period by migration 183 for pre-migration rows.';
