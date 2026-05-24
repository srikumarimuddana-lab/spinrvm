-- 103_backfill_incentive_claims.sql
-- One-time backfill: create ride_incentive_claims for completed rides that
-- matched an active incentive but were completed before the claim-recording
-- code existed.
--
-- Matching logic mirrors complete_ride in drivers.py:
--   - incentive.is_active = true
--   - service_area_id is null OR matches ride
--   - vehicle_type_id is null OR matches ride
--   - incentive was active at ride completion time (start_date/end_date)
--   - no duplicate claim for same ride + incentive
--
-- driver_earnings is NOT updated here — incentive amounts are derived from
-- ride_incentive_claims at query time (via the enrichment in ride history
-- and ride detail endpoints). This avoids double-counting.
--
-- Rollback:
--   DELETE FROM ride_incentive_claims
--   WHERE ride_id IN (SELECT id FROM rides WHERE status = 'completed');

INSERT INTO ride_incentive_claims (id, ride_id, driver_id, incentive_id, bonus_amount, claimed_at)
SELECT
    gen_random_uuid(),
    r.id,
    r.driver_id,
    i.id,
    i.bonus_amount,
    COALESCE(r.ride_completed_at, r.created_at)
FROM rides r
CROSS JOIN ride_incentives i
WHERE r.status = 'completed'
  AND r.driver_id IS NOT NULL
  AND i.is_active = TRUE
  AND i.bonus_amount > 0
  AND (i.service_area_id IS NULL OR i.service_area_id = r.service_area_id)
  AND (i.vehicle_type_id IS NULL OR i.vehicle_type_id = r.vehicle_type_id)
  AND (i.start_date IS NULL OR i.start_date <= COALESCE(r.ride_completed_at, r.created_at))
  AND (i.end_date IS NULL OR i.end_date >= COALESCE(r.ride_completed_at, r.created_at))
  AND NOT EXISTS (
      SELECT 1 FROM ride_incentive_claims c
      WHERE c.ride_id = r.id AND c.incentive_id = i.id
  );
