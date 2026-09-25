-- 469_settings_dispatch_expanded_radius.sql
--
-- Wider second search pass. The dispatch radius is fixed today
-- (service_areas.search_radius_km, else settings.search_radius_km). In a
-- thin-supply launch a driver a few km past that radius is better than a
-- cancelled ride.
--
-- With dispatch_expanded_radius_enabled = true, once a ride has been
-- searching for dispatch_expanded_radius_after_seconds, each dispatch attempt
-- searches min(search_radius_km * dispatch_expanded_radius_multiplier,
-- dispatch_expanded_radius_max_km) instead of search_radius_km.
--
-- No fare change: no long-pickup fee is added. That is an open product and
-- money decision, deliberately out of scope here.
--
-- Plan: .claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md (Phase 4)
--
-- Default false: nothing changes until an admin turns it on.
--
-- Rollback:
--   UPDATE settings SET dispatch_expanded_radius_enabled = false;  -- behavioural rollback, no deploy
--   ALTER TABLE settings DROP COLUMN IF EXISTS dispatch_expanded_radius_enabled;
--   ALTER TABLE settings DROP COLUMN IF EXISTS dispatch_expanded_radius_after_seconds;
--   ALTER TABLE settings DROP COLUMN IF EXISTS dispatch_expanded_radius_multiplier;
--   ALTER TABLE settings DROP COLUMN IF EXISTS dispatch_expanded_radius_max_km;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS dispatch_expanded_radius_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS dispatch_expanded_radius_after_seconds INT NOT NULL DEFAULT 45
        CONSTRAINT settings_dispatch_expanded_radius_after_range
        CHECK (dispatch_expanded_radius_after_seconds BETWEEN 0 AND 300),
    ADD COLUMN IF NOT EXISTS dispatch_expanded_radius_multiplier NUMERIC(3,2) NOT NULL DEFAULT 1.50
        CONSTRAINT settings_dispatch_expanded_radius_multiplier_range
        CHECK (dispatch_expanded_radius_multiplier BETWEEN 1.00 AND 3.00),
    ADD COLUMN IF NOT EXISTS dispatch_expanded_radius_max_km NUMERIC(5,1) NOT NULL DEFAULT 20.0
        CONSTRAINT settings_dispatch_expanded_radius_max_km_range
        CHECK (dispatch_expanded_radius_max_km BETWEEN 1 AND 100);

COMMENT ON COLUMN public.settings.dispatch_expanded_radius_enabled IS
    'When true, dispatch searches a wider radius once a ride has been searching for dispatch_expanded_radius_after_seconds. Default false.';
COMMENT ON COLUMN public.settings.dispatch_expanded_radius_after_seconds IS
    'Seconds a ride must have been searching before the wider radius applies. Range 0-300.';
COMMENT ON COLUMN public.settings.dispatch_expanded_radius_multiplier IS
    'Wider radius = search_radius_km x this, capped at dispatch_expanded_radius_max_km. Range 1.0-3.0.';
COMMENT ON COLUMN public.settings.dispatch_expanded_radius_max_km IS
    'Hard cap in km for the wider radius. Range 1-100 (same bound as search_radius_km).';
