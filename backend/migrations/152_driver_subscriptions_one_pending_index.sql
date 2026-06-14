-- Migration 152: one pending checkout per driver
--
-- Prevents two concurrent /drivers/subscription/subscribe requests from both
-- inserting a pending driver_subscriptions row (each backing a live Checkout
-- URL), which could let a stale session activate over a newer one. The endpoint
-- already supersedes its OWN prior pending rows before inserting, so this unique
-- index only bites the true simultaneous race; subscribe_to_plan catches the
-- resulting unique violation and returns 409.
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_driver_subscriptions_one_pending;

-- Collapse any pre-existing duplicate pending rows to one (keep the newest)
-- before the unique index is created, otherwise creation fails.
UPDATE public.driver_subscriptions
SET status = 'superseded'
WHERE status = 'pending'
  AND id NOT IN (
      SELECT DISTINCT ON (driver_id) id
      FROM public.driver_subscriptions
      WHERE status = 'pending'
      ORDER BY driver_id, created_at DESC
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_driver_subscriptions_one_pending
    ON public.driver_subscriptions (driver_id)
    WHERE status = 'pending';

COMMENT ON INDEX idx_driver_subscriptions_one_pending IS
    'At most one pending checkout per driver — closes the concurrent '
    'double-checkout race in subscribe_to_plan.';
