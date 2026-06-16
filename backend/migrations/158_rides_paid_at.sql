-- 158_rides_paid_at.sql
--
-- Create the missing rides.paid_at column.
--
-- rides.paid_at is WRITTEN by routes/webhooks.py (payment_intent.succeeded sets
-- paid_at = now()) but was never created by any migration — a latent schema gap.
-- (Note: 52_fare_split_pay_rpc.sql and routes/fare_split.py write paid_at on
-- fare_split_participants, a DIFFERENT table that does have the column.)
-- The webhook path only started exercising this after the stripe-python v15
-- crash fix (previously every webhook 500'd before reaching the ride update),
-- which surfaced "Could not find the 'paid_at' column of 'rides'" (PGRST204) as
-- a 503 on every payment_intent.succeeded delivery.
--
-- paid_at records the instant a ride's payment was confirmed (distinct from
-- updated_at, which churns on every write). Nullable — historical paid rides
-- pre-dating this column simply have NULL.
--
-- RLS: no new table — rides already has RLS; a nullable column inherits it.
-- Forward-compatible: ADD COLUMN IF NOT EXISTS is metadata-only (no rewrite) and
-- a no-op if the column was added out-of-band in some environments.
--
-- Rollback:
--   ALTER TABLE public.rides DROP COLUMN IF EXISTS paid_at;

ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS paid_at timestamptz;
