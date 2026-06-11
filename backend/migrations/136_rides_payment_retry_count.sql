-- 136_rides_payment_retry_count.sql
-- Add payment_retry_count to rides so the payment-retry background loop can
-- perform an atomic optimistic-lock claim (filter on current count, increment
-- on success). Without this column every Supabase UPDATE from payment_retry.py
-- raises PGRST204 / 42703 and the retry loop silently fails.
--
-- Column semantics (see utils/payment_retry.py):
--   0     — never attempted a retry
--   1..3  — number of Stripe PaymentIntent.confirm retries issued
--   3 (MAX_RETRIES) — exhausted; admin alert sent; no further retries
--
-- Rollback: ALTER TABLE public.rides DROP COLUMN IF EXISTS payment_retry_count;

ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS payment_retry_count INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.rides.payment_retry_count IS
    'Number of payment retry attempts issued by the background payment-retry loop. '
    'Used as an optimistic-lock counter: the retry loop filters on the current value '
    'and increments atomically so two replicas cannot double-charge the same ride.';
