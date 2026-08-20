-- 353: rollout flag for the ride-less SOS path (ACTION_ITEMS.md B15(c)).
--
-- Context: POST /rides/{ride_id}/emergency (backend/routes/rides/safety.py
-- trigger_emergency) requires an active ride and 404s without one, so a
-- rider who feels unsafe before booking or after drop-off has no in-app SOS
-- path -- only a client-side prompt to call 911 directly. This adds the
-- dark-launch gate for the new sibling endpoint (POST /rides/emergency,
-- ride_id = NULL) that closes that gap for rider-app.
--
-- Fixed-flat-column `settings` table (migration 313's own header: "there is
-- no JSON catch-all") -- a new AppSettings/admin-PATCH field is not safe to
-- add without a matching migration, or PUT /api/admin/settings 500s on the
-- unknown column. See migration 313 and agents/runs/sos-rideless-path/
-- challenges-and-issues.md for the prior incident this exact failure mode
-- caused.
--
-- Default false per the ship-dark rule, mirroring driver_discreet_sos_enabled
-- (migration/B16 precedent) and route_booked_dropoff_anchor_enabled (349).
-- The new backend endpoint also checks this flag itself and 404s when off --
-- fail-closed defense in depth, not just "the client won't call it".
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS rideless_sos_enabled;
--
-- Forward-compatible: additive defaulted column; older backends ignore it.

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS rideless_sos_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.settings.rideless_sos_enabled IS
    'Dark-launch gate for POST /rides/emergency (ride-less SOS, '
    'ACTION_ITEMS.md B15(c)). Off = the endpoint 404s and rider-app keeps '
    'showing the existing "No Active Ride / Call 911" prompt when no ride '
    'is active. On = rider-app''s home-screen SOS button can send a real '
    'ride-less alert (safety_incidents.ride_id = NULL, '
    'category=''sos_button_rideless''). SMS/push copy and triage-runbook '
    'readiness were reviewed and signed off 2026-08-20 -- see '
    'agents/runs/sos-rideless-path/decisions.md. Still ships false here; '
    'enabling in any real environment is a separate PATCH /api/admin/settings '
    'action, not part of this migration.';
