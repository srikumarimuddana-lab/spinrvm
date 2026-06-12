-- 146_settings_stale_intent_hours.sql
--
-- Tuning knob for the stale-intent reconciler (utils/stale_intent_reconciler.py).
-- The loop flips drivers.is_online=false for drivers whose app has been
-- unreachable for this many hours; the code default is 4 when the column is
-- NULL or absent, so this migration only makes the value operator-tunable.
--
-- Lives in the settings wide-table (one column per setting) so operators can
-- change it from the admin Settings API / Supabase table editor and have it
-- take effect within the 60s settings cache TTL — no redeploy.
--
-- Range 1-48: below 1h would re-create the retired presence_sweeper's
-- failure mode (flipping on transient gaps); above 48h the insurance-period
-- audit gap this loop exists to close becomes meaningless.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS stale_intent_offline_hours;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS stale_intent_offline_hours NUMERIC DEFAULT 4
        CONSTRAINT settings_stale_intent_hours_range CHECK (stale_intent_offline_hours BETWEEN 1 AND 48);

COMMENT ON COLUMN public.settings.stale_intent_offline_hours IS
    'Hours a driver''s app must be unreachable (no location batch / presence) before the stale-intent reconciler flips is_online=false and logs insurance Period 0. Code default 4. Range 1-48.';
