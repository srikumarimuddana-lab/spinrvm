-- Migration 184: Backfill subscriber_count on subscription_plans
--
-- Migration 181 added subscriber_count INTEGER NOT NULL DEFAULT 0. On databases
-- that already had active driver_subscriptions rows (plans sold before the column
-- was added), every plan starts at 0 and future increments continue from that
-- wrong baseline — admin plan lists display zero subscribers and the count
-- never catches up.
--
-- This migration recomputes the count from the current state of driver_subscriptions.
-- It counts only rows that represent a paid subscription (active or expired).
-- Excluded: 'pending' (checkout started, payment not confirmed),
--           'superseded' (replaced by an upgrade/renewal),
--           'cancelled' (driver cancelled or refunded).
-- This matches the intent of subscriber_count increments in the application
-- code, which only fires on Stripe webhook activation (not on checkout creation).
--
-- Rollback: UPDATE public.subscription_plans SET subscriber_count = 0;
-- (safe to re-run: DEFAULT 0 is the same as resetting)

UPDATE public.subscription_plans p
SET subscriber_count = sub_counts.cnt
FROM (
    SELECT plan_id, COUNT(*) AS cnt
    FROM public.driver_subscriptions
    WHERE status IN ('active', 'expired')
    GROUP BY plan_id
) sub_counts
WHERE p.id = sub_counts.plan_id;
