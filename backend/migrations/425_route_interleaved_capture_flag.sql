-- Default-off rollout for bounded foreground/background capture reordering.
-- Operational activation is via the trusted settings row, not a mobile flag.
-- Rollback without redeploy:
--   UPDATE public.settings SET route_interleaved_capture_enabled = false
--   WHERE id = 'app_settings';
-- Allow the existing 60-second settings cache to expire. In-flight finalizers
-- may complete with the previous value. This does not undo published route or
-- distance revisions; do not rewrite settled fares or raw GPS evidence.
-- Optional schema rollback after all code readers have been retired:
--   ALTER TABLE public.settings DROP COLUMN route_interleaved_capture_enabled;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS route_interleaved_capture_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.route_interleaved_capture_enabled IS
    'Default-off route finalizer rollout: preserve native foreground/background '
    'capture overlap up to 10 seconds while retaining per-source clock checks. '
    'Affects new route/distance revisions only; does not repair missing GPS fixes. '
    'Enable only after staging evidence review.';
