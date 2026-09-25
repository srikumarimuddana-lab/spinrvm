-- 468_settings_ride_search_timeout.sql
--
-- Makes the on-demand "no driver found" search window configurable. It is
-- hard-coded to 5 minutes today in three places that must agree:
--   * routes/rides/matching.py  ride_search_timeout(timeout_seconds=300)
--   * utils/stuck_ride_sweeper.py _SEARCHING_TIMEOUT_MINUTES = 5 (durable backstop)
--   * routes/rides/matching.py  _MAX_DISPATCH_ATTEMPTS = 30 (30 x 10 s retry)
--
-- Default 300 keeps today's behaviour on deploy. The product decision
-- (2026-09-25) is 180 s for launch; an admin sets it in Settings.
-- Scheduled rides keep their own deadline (utils/scheduled_ride_config.py).
--
-- Upper bound 300, not higher: ride_offers has UNIQUE (ride_id, driver_id)
-- (migration 100) and the offer-skip key lasts 300 s. A longer search would
-- re-rank a driver already offered this ride; on the PostgREST claim path the
-- ride_offers insert then fails and releases the whole batch. Raise this only
-- together with a fix for that constraint.
--
-- Plan: .claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md (Phase 2)
--
-- Rollback:
--   UPDATE settings SET ride_search_timeout_seconds = 300;  -- behavioural rollback, no deploy
--   ALTER TABLE settings DROP COLUMN IF EXISTS ride_search_timeout_seconds;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS ride_search_timeout_seconds INT NOT NULL DEFAULT 300
        CONSTRAINT settings_ride_search_timeout_range
        CHECK (ride_search_timeout_seconds BETWEEN 90 AND 300);

COMMENT ON COLUMN public.settings.ride_search_timeout_seconds IS
    'Seconds an on-demand ride may stay in searching before it is auto-cancelled as no_drivers_found. Range 90-300, default 300. Scheduled rides use their own deadline.';
