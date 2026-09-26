-- Default-off rollout for the unified driver-app toast (UX program W2.3).
-- When true, /drivers/config serves driver_unified_toast_enabled=true and the
-- driver app's showToast (hooks/useToast.ts) renders the unified toast (a
-- driver-app copy of the rider app's banner) instead of
-- react-native-toast-message. Callers, copy and the 3.5 s display time are
-- unchanged; only the banner implementation moves.
-- Rollback (operational, no mobile release):
-- UPDATE public.settings SET driver_unified_toast_enabled = false
-- WHERE id = 'app_settings';
-- Settings cache is 60s; the driver app picks the value up on its next
-- /drivers/config read (dashboard mount or refetch), and the next toast after
-- that renders through react-native-toast-message as before.
-- Optional schema rollback, after retiring readers:
-- ALTER TABLE public.settings DROP COLUMN driver_unified_toast_enabled;
-- Existing table permissions/RLS remain unchanged. No new query or index.
SET lock_timeout = '5s';
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS driver_unified_toast_enabled BOOLEAN NOT NULL DEFAULT FALSE;
RESET lock_timeout;
COMMENT ON COLUMN public.settings.driver_unified_toast_enabled IS
    'Canary for rendering driver-app toasts with the unified toast (a '
    'driver-app copy of the rider app''s banner). False keeps '
    'react-native-toast-message. Driver app only; the rider app ignores it.';
