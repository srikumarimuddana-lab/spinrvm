-- 376: per-service-area rollout switch for ride-incentive eligibility.
--
-- Migration 375 added settings.incentive_eligibility_enforced, a single global
-- switch: enforcement of start_date/end_date, the conditions JSONB,
-- bonus_type='percentage' and the max_budget cap was all-fleet or nothing.
-- Incentives are configured per service area (ride_incentives.service_area_id,
-- managed in the admin dashboard's per-area Incentives tab), so the switch that
-- governs them belongs at the same granularity — and a city-by-city rollout is
-- how this should reach production, given it changes what drivers are PAID.
--
-- Resolution is OR, not AND:
--     enforce = settings.incentive_eligibility_enforced
--               OR service_areas.incentive_eligibility_enforced
-- The per-area column is the staged rollout; the global one stays as the
-- fleet-wide master switch (flip it once every area is verified). AND would
-- have made a freshly-enabled area silently do nothing until the global was
-- also on, which reads as a broken toggle.
--
-- A ride with NO service_area_id can only be governed by the global switch —
-- there is no area row to carry a per-area flag. Those rides match only
-- globally-scoped incentives (service_area_id IS NULL) in the first place.
--
-- Known limitation while the fleet is partially enabled: a globally-scoped
-- incentive's max_budget is one shared pot across every area. Rides in an
-- area that is still unenforced keep drawing on that pot without a cap check,
-- so the cap is only fully honoured once every area is on (or the global
-- switch is). Documented rather than worked around: the alternative is
-- refusing partial rollout entirely, which is worse for a payout change.
--
-- Rollback:
--   ALTER TABLE service_areas DROP COLUMN IF EXISTS incentive_eligibility_enforced;
--
-- Forward-compatible: additive defaulted column. An older backend ignores it
-- and keeps reading the global switch alone, which is exactly the pre-376
-- behaviour.

ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS incentive_eligibility_enforced boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN service_areas.incentive_eligibility_enforced IS
    'When true, ride-incentive matching for rides in this area honours start_date/'
    'end_date, the conditions JSONB, bonus_type=percentage and the max_budget cap. '
    'ORed with settings.incentive_eligibility_enforced (the fleet-wide master '
    'switch). Default false preserves pre-376 behaviour. See migration 376.';
