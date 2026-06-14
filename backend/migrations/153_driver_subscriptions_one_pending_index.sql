-- Migration 153: at most one pending checkout per driver (partial unique index)
--
-- Closes the concurrent double-checkout race in subscribe_to_plan: two
-- simultaneous requests that both pass the supersede step would otherwise each
-- insert a pending row (each backing a live Checkout URL). This index makes the
-- second insert fail; the endpoint catches it and returns 409.
--
-- driver_subscriptions is written by the webhook path, the subscribe endpoint,
-- and the expiry loop concurrently with production traffic, so the index is
-- built CONCURRENTLY (no SHARE lock blocking those writes). The runner detects
-- CONCURRENTLY and executes this file statement-by-statement in autocommit.
-- The DROP CONCURRENTLY IF EXISTS first clears any INVALID leftover from an
-- interrupted prior build (IF NOT EXISTS alone would skip rebuilding it).
-- Migration 152 already demoted duplicate pending rows so the build can't fail
-- on existing data.
--
-- Rollback: DROP INDEX CONCURRENTLY IF EXISTS idx_driver_subscriptions_one_pending;

DROP INDEX CONCURRENTLY IF EXISTS idx_driver_subscriptions_one_pending;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_driver_subscriptions_one_pending
    ON public.driver_subscriptions (driver_id)
    WHERE status = 'pending';
