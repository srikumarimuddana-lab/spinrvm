-- 311_settings_heatmap_config.sql
--
-- Rollback:
--   ALTER TABLE public.settings
--       DROP COLUMN IF EXISTS driver_heatmap_enabled,
--       DROP COLUMN IF EXISTS heatmap_k_floor,
--       DROP COLUMN IF EXISTS heatmap_cell_lat_deg,
--       DROP COLUMN IF EXISTS heatmap_cell_lng_deg,
--       DROP COLUMN IF EXISTS heatmap_decay_half_life_days,
--       DROP COLUMN IF EXISTS heatmap_refresh_seconds;
--   (driver_heatmap_v2_enabled / heatmap_internal_driver_ids are kept — they
--    are read by the driver endpoint and defaulting them away would silently
--    re-enable v2 for nobody but also drop an allowlist an operator set.)
--
-- Why this migration exists
-- ------------------------
-- HM-13 / AD-05 shipped an admin UI that PUTs seven heatmap config keys to
-- /api/admin/settings, and a driver endpoint that reads them from the merged
-- app-settings dict — but no migration ever added the columns, and the admin
-- request model did not declare the fields. The write was silently dropped at
-- validation, the endpoint returned 200, and the audit row recorded
-- changed_keys: []. Net effect: the entire heatmap config surface (including
-- the k-anonymity floor, a privacy control) was unsettable through every
-- supported path, while the UI reported success.
--
-- This migration is the persistence half of that fix. The API half lives in
-- backend/routes/admin/settings.py (SettingsUpdateRequest) and
-- backend/schemas.py (AppSettings).
--
-- Additive only: every column is nullable-with-default, so PG11+ applies the
-- default as metadata (no table rewrite, no lock beyond a brief ACCESS
-- EXCLUSIVE for the catalog update). `settings` is a single-row config table,
-- so even a rewrite would be trivial. Existing readers are unaffected —
-- defaults match the hardcoded fallbacks the code already used.

ALTER TABLE public.settings
    -- Global master kill switch for the driver demand heatmap. Checked before
    -- the per-area service_areas.show_demand_heatmap toggle, so ops can take
    -- the whole feature down fleet-wide in one flip without touching every
    -- area. Default true = preserve today's behaviour (per-area toggle governs).
    ADD COLUMN IF NOT EXISTS driver_heatmap_enabled        BOOLEAN NOT NULL DEFAULT TRUE,

    -- k-anonymity floor: cells with fewer than this many rides are suppressed
    -- from the payload entirely. PIPEDA control — do not default below 3.
    ADD COLUMN IF NOT EXISTS heatmap_k_floor               INTEGER NOT NULL DEFAULT 3,

    -- Grid cell size in degrees (~0.004 lat ≈ 445 m, ~0.006 lng ≈ 410 m at
    -- Saskatoon's latitude). Smaller cells = finer detail but weaker
    -- k-anonymity for the same ride volume.
    ADD COLUMN IF NOT EXISTS heatmap_cell_lat_deg          NUMERIC(8,6) NOT NULL DEFAULT 0.004,
    ADD COLUMN IF NOT EXISTS heatmap_cell_lng_deg          NUMERIC(8,6) NOT NULL DEFAULT 0.006,

    -- Recency decay: a ride's weight halves every N days within the 7-day
    -- window, so "busy now" outranks "was busy last week".
    ADD COLUMN IF NOT EXISTS heatmap_decay_half_life_days  NUMERIC(6,2) NOT NULL DEFAULT 3,

    -- Poll interval served to the driver app. Floored at 30 s server-side in
    -- routes/drivers/profile.py as well: this value multiplies across every
    -- online driver, so an unclamped small number is a self-inflicted DoS.
    ADD COLUMN IF NOT EXISTS heatmap_refresh_seconds       INTEGER NOT NULL DEFAULT 90;

-- Bounds enforced at the DB layer too, so a direct psql/Supabase-console edit
-- (which bypasses the admin API's Pydantic validation entirely) cannot park a
-- value that disables the privacy floor or weaponises the driver fleet's poll
-- rate. The application clamps as well — defence in depth, not either/or.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'settings_heatmap_bounds_chk'
    ) THEN
        ALTER TABLE public.settings
            ADD CONSTRAINT settings_heatmap_bounds_chk CHECK (
                heatmap_k_floor              BETWEEN 1 AND 50
                AND heatmap_cell_lat_deg     BETWEEN 0.0005 AND 0.05
                AND heatmap_cell_lng_deg     BETWEEN 0.0005 AND 0.05
                AND heatmap_decay_half_life_days BETWEEN 0.5 AND 30
                AND heatmap_refresh_seconds  BETWEEN 30 AND 600
            );
    END IF;
END $$;

COMMENT ON COLUMN public.settings.driver_heatmap_enabled IS
    'Global kill switch for the driver demand heatmap. False = feature off fleet-wide regardless of per-area show_demand_heatmap.';
COMMENT ON COLUMN public.settings.heatmap_k_floor IS
    'PIPEDA k-anonymity floor: minimum rides per cell before it may appear in any heatmap payload.';
