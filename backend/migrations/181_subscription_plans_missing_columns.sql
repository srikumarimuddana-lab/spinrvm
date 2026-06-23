-- Migration 181: Add missing columns to subscription_plans
--
-- Root cause: migration 08_complete_schema.sql created subscription_plans with only
-- a minimal schema (id, name, price, billing_period, features, is_active, created_at).
-- Migration 09_subscription_plans.sql used CREATE TABLE IF NOT EXISTS, which was
-- silently skipped because the table already existed — so the columns it defined
-- (duration_days, rides_per_day, description, vehicle_types, service_areas,
-- subscriber_count, updated_at) were never applied to existing databases.
--
-- This migration adds the missing columns idempotently. Migration 150 already
-- handled stripe_price_id via ALTER TABLE, so that one is excluded here.
--
-- Rollback:
--   ALTER TABLE public.subscription_plans
--     DROP COLUMN IF EXISTS duration_days,
--     DROP COLUMN IF EXISTS rides_per_day,
--     DROP COLUMN IF EXISTS description,
--     DROP COLUMN IF EXISTS vehicle_types,
--     DROP COLUMN IF EXISTS service_areas,
--     DROP COLUMN IF EXISTS subscriber_count,
--     DROP COLUMN IF EXISTS updated_at;

ALTER TABLE public.subscription_plans
    ADD COLUMN IF NOT EXISTS duration_days    INTEGER     NOT NULL DEFAULT 30,
    ADD COLUMN IF NOT EXISTS rides_per_day    INTEGER     NOT NULL DEFAULT -1,
    ADD COLUMN IF NOT EXISTS description      TEXT        DEFAULT '',
    ADD COLUMN IF NOT EXISTS vehicle_types    JSONB       DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS service_areas    JSONB       DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS subscriber_count INTEGER     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMPTZ;

COMMENT ON COLUMN public.subscription_plans.duration_days IS
    'Plan length in days: 1=daily, 7=weekly, 30=monthly.';
COMMENT ON COLUMN public.subscription_plans.rides_per_day IS
    'Max rides per calendar day. -1 = unlimited.';
COMMENT ON COLUMN public.subscription_plans.subscriber_count IS
    'Cached count of active subscribers on this plan. Incremented on subscribe, decremented on cancel/expire.';
