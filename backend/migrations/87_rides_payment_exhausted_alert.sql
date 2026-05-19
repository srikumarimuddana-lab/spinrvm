-- 87_rides_payment_exhausted_alert.sql
-- Rollback: ALTER TABLE rides DROP COLUMN IF EXISTS admin_alerted_payment_exhausted;
--           DROP INDEX IF EXISTS idx_rides_rider_completed_failed;

-- Claim flag for replay-safe admin alerting when payment retries exhaust.
ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS admin_alerted_payment_exhausted boolean NOT NULL DEFAULT false;

-- Partial index for the unpaid-ride booking block query in POST /rides
-- and the admin GET /admin/rides/unpaid endpoint.
CREATE INDEX IF NOT EXISTS idx_rides_rider_completed_failed
  ON rides (rider_id, status, payment_status)
  WHERE status = 'completed' AND payment_status = 'failed';
