-- Migration 92: payouts — instant payout fields
--
-- Why:
--   Adds the columns needed for instant (same-day) payouts via Stripe's
--   instant-payout method. Standard scheduled payouts (the existing flow)
--   leave payout_type NULL and fee = 0, so this migration is backwards-
--   compatible with every existing row.
--
--   payout_type:    "standard" (default, Stripe's daily/weekly schedule) or
--                   "instant" (Stripe Instant Payout API, fee-bearing).
--   fee:            the fee charged on the gross amount, in dollars. Zero
--                   for standard payouts.
--   net_amount:     amount - fee. The dollars that actually land in the
--                   driver's bank account. Null for older standard rows
--                   where the column didn't exist; callers should fall back
--                   to amount for those.
--
-- Rollback plan:
--   ALTER TABLE payouts
--       DROP COLUMN IF EXISTS payout_type,
--       DROP COLUMN IF EXISTS fee,
--       DROP COLUMN IF EXISTS net_amount;

ALTER TABLE payouts
    ADD COLUMN IF NOT EXISTS payout_type TEXT DEFAULT 'standard',
    ADD COLUMN IF NOT EXISTS fee         NUMERIC(10, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS net_amount  NUMERIC(10, 2) DEFAULT NULL;

-- Backfill payout_type for clarity in admin dashboards. Idempotent.
UPDATE payouts SET payout_type = 'standard' WHERE payout_type IS NULL;

-- Index supports the driver-app "instant payouts only" tab/filter that
-- ships alongside the new endpoint.
CREATE INDEX IF NOT EXISTS idx_payouts_driver_type_created
    ON payouts (driver_id, payout_type, created_at DESC);
