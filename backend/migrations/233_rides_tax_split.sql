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
-- for the platform portion (utils/stripe_tax.py). NULL until recorded; only
-- completed+paid rides are ever stamped.
--
-- Rollback plan: both columns are additive and nullable — safe to
--   ALTER TABLE rides DROP COLUMN tax_split,
--                     DROP COLUMN stripe_tax_transaction_id;
-- No data migration required either direction; legacy rides simply have NULL
-- tax_split and readers fall back to the undivided tax_amount.
-- If the CONCURRENTLY index build below fails midway it leaves an INVALID
-- index: `DROP INDEX CONCURRENTLY idx_rides_stripe_tax_pending;` then re-run.

ALTER TABLE rides ADD COLUMN IF NOT EXISTS tax_split JSONB;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS stripe_tax_transaction_id TEXT;

-- Recording backlog scan (utils/stripe_tax.py::_tick). Predicate mirrors the
-- recorder's full filter — tax_split is written at BOOKING, so without the
-- status/payment_status predicates every cancelled or never-paid ride would
-- sit in the index forever; scoped this way it self-limits to the true
-- settlement backlog. CONCURRENTLY: rides is a high-write table and a plain
-- CREATE INDEX would block inserts/updates for the build duration (the
-- migration runner detects CONCURRENTLY and runs it in autocommit mode).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_stripe_tax_pending
    ON rides (completed_at)
    WHERE status = 'completed' AND payment_status = 'paid'
      AND tax_split IS NOT NULL AND stripe_tax_transaction_id IS NULL;
