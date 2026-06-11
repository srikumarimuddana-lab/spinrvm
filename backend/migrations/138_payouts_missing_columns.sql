-- 138_payouts_missing_columns.sql
-- Add four columns that production code references but were never present in
-- the payouts table:
--
--   retry_count   INTEGER NOT NULL DEFAULT 0
--       Used by utils/payment_retry.py (retry_stuck_payouts). The Python code
--       reads payout.get("retry_count", 0) to gate MAX_RETRIES and then writes
--       the incremented value back via update_one. Without the column the
--       get_rows("payouts", {"status": "pending"}) call fails with Postgres
--       42703 before any Python-level dict access happens.
--
--   updated_at    TIMESTAMPTZ (nullable)
--       Written by payment_retry.py on every payout status/count update.
--       Nullable so existing rows are not rejected; callers always supply the
--       value in INSERT/UPDATE.
--
--   payout_type   TEXT DEFAULT 'standard'
--   fee           NUMERIC(10,2) DEFAULT 0
--   net_amount    NUMERIC(10,2) DEFAULT NULL
--       These three were supposed to land with migration 92_payouts_instant.sql
--       but that migration was not applied to the production database (the
--       runner stopped on an earlier failure). They are reproduced here with
--       IF NOT EXISTS so this migration is safe to run even if 92 is later
--       applied — each ADD COLUMN becomes a no-op.
--       Routes/drivers.py references payout_type in every payout INSERT.
--
-- Rollback:
--   ALTER TABLE public.payouts
--       DROP COLUMN IF EXISTS retry_count,
--       DROP COLUMN IF EXISTS updated_at,
--       DROP COLUMN IF EXISTS payout_type,
--       DROP COLUMN IF EXISTS fee,
--       DROP COLUMN IF EXISTS net_amount;
--   DROP INDEX IF EXISTS idx_payouts_driver_type_created;

ALTER TABLE public.payouts
    ADD COLUMN IF NOT EXISTS retry_count  INTEGER         NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS payout_type  TEXT            DEFAULT 'standard',
    ADD COLUMN IF NOT EXISTS fee          NUMERIC(10, 2)  DEFAULT 0,
    ADD COLUMN IF NOT EXISTS net_amount   NUMERIC(10, 2)  DEFAULT NULL;

-- Backfill payout_type for rows that pre-date this column. Idempotent.
UPDATE public.payouts SET payout_type = 'standard' WHERE payout_type IS NULL;

-- Index for the driver-app "instant payouts only" tab / filter
-- (mirrors the index in 92_payouts_instant.sql; IF NOT EXISTS makes it safe).
CREATE INDEX IF NOT EXISTS idx_payouts_driver_type_created
    ON public.payouts (driver_id, payout_type, created_at DESC);

COMMENT ON COLUMN public.payouts.retry_count IS
    'Number of payment-retry attempts made by retry_stuck_payouts(). Gated by MAX_RETRIES (3). Reset to 0 on success.';

COMMENT ON COLUMN public.payouts.updated_at IS
    'Last-modified timestamp; set by the backend on every status or retry_count change.';

COMMENT ON COLUMN public.payouts.payout_type IS
    '"standard" (Stripe daily/weekly schedule) or "instant" (Stripe Instant Payout API, fee-bearing). NULL treated as standard by callers.';

COMMENT ON COLUMN public.payouts.fee IS
    'Fee charged on the gross payout amount (dollars). Zero for standard payouts.';

COMMENT ON COLUMN public.payouts.net_amount IS
    'amount − fee. Dollars that land in the driver bank account. NULL for pre-92 standard rows; callers fall back to amount when NULL.';
