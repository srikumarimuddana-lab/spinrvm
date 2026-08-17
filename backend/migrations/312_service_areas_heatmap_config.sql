-- 312_service_areas_heatmap_config.sql
--
-- Rollback:
--   ALTER TABLE public.service_areas DROP COLUMN IF EXISTS heatmap_config;
--   (Additive and defaulted to '{}'; dropping it returns every area to the
--    global settings values, which is the pre-migration behaviour exactly.)
--
-- Per-service-area heatmap tuning.
-- --------------------------------
-- Migration 311 made the heatmap knobs settable globally. That is still the
-- wrong granularity for several of them: the aggregation windows were tuned
-- for one mid-size market and do not transfer between regions.
--
--   * A dense downtown wants a shorter "busy now" window and smaller cells
--     than a sparse rural area covering the same ride volume.
--   * A low-volume region needs a LONGER baseline window to clear the same
--     k-anonymity floor a busy region clears in a day. Forcing one global
--     window means either starving small markets of visible cells, or
--     lowering the privacy floor everywhere to compensate — the second is
--     not an acceptable trade for a PIPEDA control.
--
-- Resolution order is area override -> global app_settings -> code default,
-- implemented in backend/utils/heatmap_config.py, with every value clamped at
-- the read site regardless of source.
--
-- Shape: a sparse JSONB object holding ONLY the keys this area overrides, e.g.
--   {"k_floor": 5, "baseline_window_days": 56}
-- Absent key = inherit. This is deliberately not a full config snapshot: an
-- area that inherits must keep tracking the global when the global changes,
-- and a snapshot would silently freeze it at whatever the value was on the
-- day someone opened the form.
--
-- Additive and non-blocking: ADD COLUMN with a constant default is a
-- metadata-only operation on PG11+ (no table rewrite). service_areas is a
-- small table (tens of rows) regardless.

ALTER TABLE public.service_areas
    ADD COLUMN IF NOT EXISTS heatmap_config JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Reject a non-object at the DB layer. Bounds for individual keys are NOT
-- checked here: the key set evolves with the application (HEATMAP_SPEC), and a
-- CHECK listing them would drift out of sync and start rejecting valid config
-- after a deploy. Per-key bounds live in the admin API's validation and in the
-- unconditional clamp at the read site, which together cover every write path
-- including direct SQL. What this constraint does guarantee is that the column
-- can always be read as an object, so the resolver never has to defend against
-- an array or a scalar landing here.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'service_areas_heatmap_config_is_object'
    ) THEN
        ALTER TABLE public.service_areas
            ADD CONSTRAINT service_areas_heatmap_config_is_object
            CHECK (jsonb_typeof(heatmap_config) = 'object');
    END IF;
END $$;

COMMENT ON COLUMN public.service_areas.heatmap_config IS
    'Sparse per-area overrides for driver-heatmap tuning (k_floor, cell sizes, decay, refresh, aggregation windows). Absent key = inherit the global settings value. Resolved by backend/utils/heatmap_config.py.';
