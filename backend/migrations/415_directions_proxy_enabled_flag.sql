-- 415: rollout flag for routing client-direct Google Directions calls
-- through a new backend proxy (docs/audit/ride-experience/ROADMAP.md R7).
--
-- Context: 6 MapViewDirections mount sites across rider-app and driver-app
-- call Google Directions directly from the device with a bundled
-- EXPO_PUBLIC_GOOGLE_MAPS_API_KEY, bypassing backend/utils/maps_budget.py's
-- daily-spend circuit breaker entirely. This adds the dark-launch gate for
-- the new GET /maps/directions proxy endpoint (backend/routes/maps_proxy.py)
-- that each of those 6 call sites can be flipped to use instead, while
-- keeping the existing on-device MapViewDirections as a same-request
-- fallback (never removed -- see the roadmap item's own "do not remove the
-- fallback outright" note).
--
-- Fixed-flat-column `settings` table (migration 313's own header: "there is
-- no JSON catch-all") -- a new AppSettings/admin-PATCH field is not safe to
-- add without a matching migration, or PUT /api/admin/settings 500s on the
-- unknown column. See migration 313/353 for the prior incident this exact
-- failure mode caused.
--
-- Default false per the ship-dark rule, mirroring rideless_sos_enabled (353)
-- and driver_discreet_sos_enabled (313/B16).
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS directions_proxy_enabled;
--
-- Forward-compatible: additive defaulted column; older backends ignore it.

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS directions_proxy_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.settings.directions_proxy_enabled IS
    'Dark-launch gate (ROADMAP.md R7) for routing rider-app/driver-app''s '
    'client-direct MapViewDirections calls through GET /maps/directions '
    '(backend proxy, budget-gated via maps_budget.py) instead of calling '
    'Google Directions directly from the device with the bundled API key. '
    'Off (default) = every call site keeps its existing on-device behavior '
    'unchanged. On = each migrated call site tries the backend proxy first '
    'and falls back to the on-device call only if the proxy request itself '
    'fails, so a proxy outage degrades to today''s behavior rather than to '
    'no route line at all. Ship dark, verify in staging/canary, then flip '
    'on -- see docs/change-log/2026-09-12-directions-proxy-r7.md.';
