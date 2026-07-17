-- 233_rides_tax_split.sql
--
-- Driver/platform GST attribution on rides, and the Stripe Tax transaction
-- linkage for the platform-remitted portion.
--
-- tax_split JSONB shape (written at booking by routes/rides/booking.py from
-- features.calculate_all_fees):
--   {
--     "driver":   {"GST": 0.55, ...},        -- tax on the driver's fare share
--     "platform": {"GST": 0.10, ...},        -- tax on booking/airport/area fees
--     "driver_total": 0.55,
--     "platform_total": 0.10,
--     "driver_taxable_base": 11.00,
--     "platform_taxable_base": 2.00
--   }
-- Invariant: driver_total + platform_total == rides.tax_amount (exact, the
-- platform side is computed as the remainder).
--
-- stripe_tax_transaction_id: the Stripe Tax Transaction recorded at settlement
-- for the platform portion (utils/stripe_tax.py). NULL until recorded; rides
-- with a platform portion and a NULL id are the recording backlog.
--
-- Rollback plan: both columns are additive and nullable — safe to
--   ALTER TABLE rides DROP COLUMN tax_split,
--                     DROP COLUMN stripe_tax_transaction_id;
-- No data migration required either direction; legacy rides simply have NULL
-- tax_split and readers fall back to the undivided tax_amount.

ALTER TABLE rides ADD COLUMN IF NOT EXISTS tax_split JSONB;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS stripe_tax_transaction_id TEXT;

-- Recording backlog scan: settled rides carrying a platform tax portion not
-- yet mirrored into Stripe Tax. Partial index keeps it cheap — the column is
-- NULL only until the recorder runs, so the index stays small.
CREATE INDEX IF NOT EXISTS idx_rides_stripe_tax_pending
    ON rides (completed_at)
    WHERE tax_split IS NOT NULL AND stripe_tax_transaction_id IS NULL;
