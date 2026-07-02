-- Migration 204: per-service-area demand-heatmap color theme
--
-- Adds a curated theme picker (not free-form colors) for the driver-app
-- demand overlay. Free-form color config would let an operator unknowingly
-- ship a ramp illegible to red-green colorblind drivers (~8% of male
-- drivers) — instead admins choose between pre-validated ramps, each
-- lightness-monotonic and CVD-checked (see
-- driver-app/components/dashboard/demandHeatmap.ts):
--
--   'inferno' (default)  deep violet -> orange -> bright yellow
--   'ocean'              deep navy -> cyan -> pale aqua
--   'viridis'            violet -> teal -> yellow-green
--
-- The CHECK mirrors HEATMAP_COLOR_THEMES in backend/utils/heatmap.py; the
-- driver app maps unknown values to 'inferno', so removing a theme later is
-- backward-safe.
--
-- Additive + defaulted -> forward-compatible with traffic in flight; no
-- backfill required. No RLS change (service_areas is service-role only).
--
-- Rollback (on paper):
--   ALTER TABLE service_areas DROP COLUMN IF EXISTS heatmap_color_theme;
--   (The endpoint falls back to 'inferno' when the column is absent.)

ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS heatmap_color_theme text NOT NULL DEFAULT 'inferno';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'service_areas_heatmap_theme_check'
    ) THEN
        ALTER TABLE service_areas
            ADD CONSTRAINT service_areas_heatmap_theme_check
            CHECK (heatmap_color_theme IN ('inferno', 'ocean', 'viridis'));
    END IF;
END $$;
