-- 466_settings_offer_expired_decline_as_miss.sql
--
-- Flag for treating the driver app's countdown-expiry auto-decline as a
-- missed offer instead of a decline.
--
-- Today, when an offer's countdown reaches 0 with the driver app in the
-- foreground, the app itself POSTs /drivers/rides/{id}/decline. The backend
-- records a decline and calls reset_miss_streak, so a driver who leaves the
-- app open and walks away never reaches auto_offline_miss_threshold. New
-- driver-app builds send {"reason": "offer_expired"} on that auto-decline.
-- With this flag on, the backend routes that request through the same
-- expiry path the server-side offer timer uses (process_expired_offer /
-- expire_offer_v2): miss streak up, auto-offline at the threshold, no
-- decline audit row.
--
-- Plan: .claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md (Phase 0)
--
-- Default false: nothing changes until an admin turns it on.
--
-- Rollback:
--   UPDATE settings SET offer_expired_decline_as_miss_enabled = false;  -- behavioural rollback, no deploy
--   ALTER TABLE settings DROP COLUMN IF EXISTS offer_expired_decline_as_miss_enabled;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS offer_expired_decline_as_miss_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.offer_expired_decline_as_miss_enabled IS
    'When true, a driver decline carrying reason offer_expired (driver-app countdown auto-decline) is processed as a missed offer: miss streak incremented, auto-offline at auto_offline_miss_threshold, no ride_declined audit row. Default false.';
