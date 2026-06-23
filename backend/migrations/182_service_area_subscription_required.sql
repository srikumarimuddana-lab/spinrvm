-- Migration 182: Add subscription_required flag to service_areas
--
-- Context: service_areas already has spinr_pass_enabled (whether Spinr Pass is
-- available in the area) and subscription_plan_ids (which plans are offered).
-- Neither field means subscriptions are *mandatory* to drive in the area.
--
-- This adds subscription_required: when TRUE, a driver without an active Spinr
-- Pass subscription is blocked from:
--   1. Going online (update_driver_status)
--   2. Receiving dispatch offers (find_candidate_drivers)
--   3. Accepting a ride (accept_ride)
--
-- Default FALSE: no existing area is affected until an admin explicitly enables it.
--
-- Rollback:
--   ALTER TABLE public.service_areas DROP COLUMN IF EXISTS subscription_required;

ALTER TABLE public.service_areas
    ADD COLUMN IF NOT EXISTS subscription_required BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.service_areas.subscription_required IS
    'When TRUE, drivers in this area must hold an active Spinr Pass subscription '
    'to go online, receive offers, or accept rides. spinr_pass_enabled must also '
    'be TRUE for subscriptions to be purchasable. Default FALSE.';
