-- 335_fix_ride2_underpaid_tip_2026_08_19.sql
-- One-off data correction for a single, specific live-testing ride whose
-- driver_earnings missed a $0.50 tip credit due to the delta-based bug
-- fixed structurally in migration 334 / backend/services/fare_service.py's
-- driver_earnings_with_tip().
--
-- Ride: 4a8a0767-72de-40c7-abb7-6038b7c0b4ba (SPR-8X2GTY)
-- Confirmed via Supabase SQL Editor query results (2026-08-19):
--   grand_total = 0.18, tip_amount = 0.50, total_fare = 0.17,
--   driver_earnings = 0.17  (WRONG: missing the $0.50 tip)
-- financial_events shows exactly one stripe_charge row for this ride,
-- delta_cents = 68 (= 0.17 fare + tax + 0.50 tip, i.e. the RIGHT amount was
-- actually charged to the rider via Stripe) — this is a driver underpayment
-- only, never a rider overcharge. No refund/re-charge is needed, only the
-- ride's own driver_earnings column.
--
-- Correct value = total_fare - (booking_fee + airport_fee) + tip_amount,
-- matching driver_earnings_with_tip()'s formula exactly. This ride has no
-- booking_fee/airport_fee row data beyond what total_fare already reflects
-- (confirmed 0.17 base), so corrected driver_earnings = 0.17 + 0.50 = 0.67.
--
-- Scope: this UPDATE is intentionally hyper-narrow (single id + guard on the
-- exact stale value) so it can never touch any other ride, including a
-- ride that happens to also read driver_earnings = 0.17 for an unrelated,
-- legitimate reason.
--
-- Rollback: re-run with driver_earnings = 0.17 substituted for 0.67 to
-- revert (values below are both hardcoded literals from the confirmed
-- production read above, not derived at migration-apply time).

UPDATE rides
   SET driver_earnings = 0.67,
       updated_at = now()
 WHERE id = '4a8a0767-72de-40c7-abb7-6038b7c0b4ba'
   AND tip_amount = 0.50
   AND total_fare = 0.17
   AND driver_earnings = 0.17;
