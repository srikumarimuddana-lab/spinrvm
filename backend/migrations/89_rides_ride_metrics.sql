-- Migration: Add ride_metrics JSONB to rides for per-phase planned-vs-actual summary.
--
-- The rides row already carries trip-level estimates (planned_distance_km,
-- duration_minutes) and actuals (actual_distance_km, phase_distances,
-- phase_durations). Per-phase ACTUAL timing is the canonical responsibility of
-- driver_insurance_periods (migration 64), which is regulatory-grade and
-- tamper-evident.
--
-- What's missing is a consolidated, rider-facing planned-vs-actual view AND a
-- place to record the pickup-leg ESTIMATE that the rider was shown at the
-- moment the driver accepted (the only piece of data with no existing home).
--
-- Rather than adding more scalars or a new table, both needs are satisfied by
-- a single JSONB column on rides:
--   * Written partially at acceptance (navigating_to_pickup estimates only).
--   * Overwritten fully at completion by reading driver_insurance_periods and
--     folding in the scalars already on the rides row.
--
-- driver_insurance_periods remains the system of record for regulatory audit
-- and analytics. ride_metrics is a derived read cache for the rider-app and
-- admin ride detail. Anyone questioning a number checks insurance_periods.
--
-- Append-only / forward-compatible: nullable column with empty default, no
-- changes to existing tables. No new RLS — rides table RLS already covers it.
--
-- Rollback:
--   ALTER TABLE rides DROP COLUMN IF EXISTS ride_metrics;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS ride_metrics JSONB DEFAULT '{}'::JSONB;

COMMENT ON COLUMN rides.ride_metrics IS
  'Consolidated planned-vs-actual summary for the ride. Written incrementally:
   partial write at driver acceptance (pickup-leg estimates), full overwrite at
   completion. Shape:
   {
     "phases": {
       "navigating_to_pickup": {"estimated_distance_km", "estimated_duration_minutes", "actual_distance_km", "actual_duration_minutes"},
       "arrived_at_pickup":    {"actual_duration_minutes"},
       "trip_in_progress":     {"estimated_distance_km", "estimated_duration_minutes", "actual_distance_km", "actual_duration_minutes"}
     },
     "totals": {"estimated_distance_km", "estimated_duration_minutes", "actual_distance_km", "actual_duration_minutes"}
   }
   System of record for per-phase timing is driver_insurance_periods (migration 64).';
