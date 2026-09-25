-- 467_settings_dispatch_reoffer.sql
--
-- Settings for re-offering a ride to drivers who DECLINED it, while never
-- re-offering it to drivers who IGNORED it (offer expired with no response).
--
-- Today every decline and every expiry writes the same Redis key,
-- spinr:offer_skip:{ride_id}:{driver_id}, with a 300 s TTL. With 10 drivers
-- and 3 offers per round, everyone has been asked by ~60 s and nobody can be
-- asked again before the 300 s auto-cancel.
--
-- With dispatch_reoffer_enabled = true:
--   * ignored (expired) offer  -> driver skipped for the rest of this ride's search
--   * 1st decline              -> driver skipped for dispatch_decline_reoffer_after_seconds,
--                                 then eligible again through the existing 10 s retry loop
--   * decline number dispatch_max_offers_per_driver_per_ride -> skipped for the rest of the search
--
-- Plan: .claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md (Phase 1)
--
-- Default false: the 300 s skip for everyone stays until an admin turns it on.
--
-- Rollback:
--   UPDATE settings SET dispatch_reoffer_enabled = false;  -- behavioural rollback, no deploy
--   ALTER TABLE settings DROP COLUMN IF EXISTS dispatch_reoffer_enabled;
--   ALTER TABLE settings DROP COLUMN IF EXISTS dispatch_decline_reoffer_after_seconds;
--   ALTER TABLE settings DROP COLUMN IF EXISTS dispatch_max_offers_per_driver_per_ride;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS dispatch_reoffer_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS dispatch_decline_reoffer_after_seconds INT NOT NULL DEFAULT 45
        CONSTRAINT settings_dispatch_decline_reoffer_after_range
        CHECK (dispatch_decline_reoffer_after_seconds BETWEEN 15 AND 120),
    ADD COLUMN IF NOT EXISTS dispatch_max_offers_per_driver_per_ride INT NOT NULL DEFAULT 2
        CONSTRAINT settings_dispatch_max_offers_per_driver_range
        CHECK (dispatch_max_offers_per_driver_per_ride BETWEEN 1 AND 3);

COMMENT ON COLUMN public.settings.dispatch_reoffer_enabled IS
    'When true, drivers who declined a ride can be offered it again after a cooldown; drivers who ignored it are not. Default false (300 s skip for all).';
COMMENT ON COLUMN public.settings.dispatch_decline_reoffer_after_seconds IS
    'Seconds after a decline before the same ride can be offered to that driver again. Range 15-120. Used only when dispatch_reoffer_enabled.';
COMMENT ON COLUMN public.settings.dispatch_max_offers_per_driver_per_ride IS
    'Maximum times one ride can be offered to one driver when they keep declining. Range 1-3. Used only when dispatch_reoffer_enabled.';
