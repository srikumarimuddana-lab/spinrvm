-- 130_ride_offers_offered_at_index.sql
-- The Driver Offers analytics endpoints (admin/analytics.py: driver-offer-stats)
-- and the per-period rides financials scan the append-only ride_offers ledger by
-- offered_at over a recent window. Migration 100 only indexed (ride_id),
-- (driver_id, status) and pending ride_ids — none cover offered_at, so every
-- analytics load would seq-scan the ledger as it grows. Add a time index.
--
-- Forward-compatible: additive index only, safe against in-flight traffic.
-- Rollback:
--   DROP INDEX IF EXISTS idx_ride_offers_offered_at;

CREATE INDEX IF NOT EXISTS idx_ride_offers_offered_at ON ride_offers (offered_at);
