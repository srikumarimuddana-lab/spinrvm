-- 428: PostHog session replay for rider-app and driver-app.
--
-- Dark-launched. Default off: neither app initialises PostHog until an admin
-- flips posthog_session_replay_enabled AND sets posthog_api_key. LogRocket is
-- left in place and is not replaced by this change.
--
-- posthog_api_key is the project API key (phc_...), the same class of
-- client-safe token as stripe_publishable_key — it is meant to ship to the
-- apps via GET /settings. Do not store a personal API key (phx_...) here.
--
-- Replay of ride screens can include map/address UI. Keep this off in
-- production until Product + privacy sign-off on PostHog Cloud region
-- (US/EU, not Canada) and masking (sensitive screens already pause capture).
--
-- Rollback (no redeploy, settings cache expires within 60 seconds):
--   UPDATE public.settings
--      SET posthog_session_replay_enabled = false
--    WHERE id = 'app_settings';
-- Schema rollback after retiring the code readers:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS posthog_session_replay_enabled;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS posthog_api_key;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS posthog_host;
--
-- Forward-compatible: additive defaulted columns; older backends ignore them.

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS posthog_session_replay_enabled BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS posthog_api_key TEXT NOT NULL DEFAULT '';

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS posthog_host TEXT NOT NULL DEFAULT 'https://us.i.posthog.com';

COMMENT ON COLUMN public.settings.posthog_session_replay_enabled IS
    'Dark-launch gate for PostHog session replay on rider-app and driver-app. '
    'Off (default) = neither app initialises PostHog. On = each app inits '
    'only when posthog_api_key is also set. Does not replace LogRocket.';

COMMENT ON COLUMN public.settings.posthog_api_key IS
    'PostHog project API key (phc_...). Client-safe; returned by GET /settings. '
    'Empty = fail closed even if posthog_session_replay_enabled is on.';

COMMENT ON COLUMN public.settings.posthog_host IS
    'PostHog ingestion host, e.g. https://us.i.posthog.com or https://eu.i.posthog.com.';
