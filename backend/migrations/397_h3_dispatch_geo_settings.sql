-- 397_h3_dispatch_geo_settings.sql
--
-- Dark-ship H3 / PostGIS dispatch candidate lookup and H3 heatmap aggregation.
-- Default provider is `legacy` (today's lat/lng bounding box). Flipping
-- dispatch_geo_provider does not require a deploy.
--
-- Also raises the PIPEDA k-anonymity floor from 1 to 3 at the DB CHECK layer
-- (application clamps already default to 3; ge=1 on the admin API was the
-- remaining hole).
--
-- Rollback:
--   ALTER TABLE public.service_areas DROP COLUMN IF EXISTS dispatch_geo_provider;
--   ALTER TABLE public.settings
--       DROP CONSTRAINT IF EXISTS settings_dispatch_geo_chk,
--       DROP CONSTRAINT IF EXISTS settings_heatmap_bounds_chk;
--   ALTER TABLE public.settings
--       DROP COLUMN IF EXISTS dispatch_geo_provider,
--       DROP COLUMN IF EXISTS dispatch_h3_resolution,
--       DROP COLUMN IF EXISTS heatmap_h3_enabled,
--       DROP COLUMN IF EXISTS heatmap_h3_resolution;
--   -- restore migration 311's looser k_floor bound:
--   ALTER TABLE public.settings ADD CONSTRAINT settings_heatmap_bounds_chk CHECK (
--       heatmap_k_floor BETWEEN 1 AND 50
--       AND heatmap_cell_lat_deg BETWEEN 0.0005 AND 0.05
--       AND heatmap_cell_lng_deg BETWEEN 0.0005 AND 0.05
--       AND heatmap_decay_half_life_days BETWEEN 0.5 AND 30
--       AND heatmap_refresh_seconds BETWEEN 30 AND 600
--   );

-- Existing rows that somehow sat at 1 or 2 would fail the new CHECK.
UPDATE public.settings
SET heatmap_k_floor = GREATEST(heatmap_k_floor, 3)
WHERE heatmap_k_floor < 3;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS dispatch_geo_provider TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS dispatch_h3_resolution INTEGER NOT NULL DEFAULT 8,
    ADD COLUMN IF NOT EXISTS heatmap_h3_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS heatmap_h3_resolution INTEGER NOT NULL DEFAULT 9;

ALTER TABLE public.service_areas
    ADD COLUMN IF NOT EXISTS dispatch_geo_provider TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'settings_dispatch_geo_chk'
    ) THEN
        ALTER TABLE public.settings
            ADD CONSTRAINT settings_dispatch_geo_chk CHECK (
                dispatch_geo_provider IN ('legacy', 'shadow', 'postgis', 'h3')
                AND dispatch_h3_resolution BETWEEN 7 AND 9
                AND heatmap_h3_resolution BETWEEN 7 AND 9
            );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'service_areas_dispatch_geo_chk'
    ) THEN
        ALTER TABLE public.service_areas
            ADD CONSTRAINT service_areas_dispatch_geo_chk CHECK (
                dispatch_geo_provider IS NULL
                OR dispatch_geo_provider IN ('legacy', 'shadow', 'postgis', 'h3')
            );
    END IF;
END $$;

ALTER TABLE public.settings DROP CONSTRAINT IF EXISTS settings_heatmap_bounds_chk;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'settings_heatmap_bounds_chk'
    ) THEN
        ALTER TABLE public.settings
            ADD CONSTRAINT settings_heatmap_bounds_chk CHECK (
                heatmap_k_floor              BETWEEN 3 AND 50
                AND heatmap_cell_lat_deg     BETWEEN 0.0005 AND 0.05
                AND heatmap_cell_lng_deg     BETWEEN 0.0005 AND 0.05
                AND heatmap_decay_half_life_days BETWEEN 0.5 AND 30
                AND heatmap_refresh_seconds  BETWEEN 30 AND 600
            );
    END IF;
END $$;
