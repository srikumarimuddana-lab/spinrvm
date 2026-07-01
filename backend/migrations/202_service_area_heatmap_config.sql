-- Migration 202: per-service-area demand-heatmap configuration
--
-- The driver demand heatmap has existed since migration 81 as a single
-- boolean (service_areas.show_demand_heatmap) gating GET /drivers/demand-heatmap,
-- which always served a hardcoded last-7-days window with no client refresh
-- contract. This migration makes the heatmap actually configurable per
-- service area from the admin dashboard:
--
--   * heatmap_data_window_hours — how far back ride demand is aggregated.
--     Default 168 (7 days) preserves current behavior exactly. Range 1..720
--     (1 hour .. 30 days) — the CHECK mirrors the pydantic validation in
--     routes/admin/service_areas.py so a direct DB write can't outrange
--     what the API allows.
--   * heatmap_refresh_seconds — how often the driver app re-fetches while
--     idle; the endpoint returns it so the client polling cadence is
--     admin-tunable without an app release. Default 300 (5 min). Range
--     30..3600 keeps a misconfigured area from hammering the API or going
--     effectively static.
--   * heatmap_data_source — which rides feed the map:
--       'all_requests'    every ride request (default; current behavior)
--       'completed_rides' completed trips only (proven, fulfilled demand)
--       'missed_rides'    cancelled requests only (unmet demand — where
--                         drivers were needed and weren't there)
--
-- Additive + defaulted → forward-compatible with in-flight traffic; no
-- backfill required. No RLS change: service_areas is read through the
-- backend service role only (existing policies unchanged).
--
-- Rollback:
--   (on paper — drop the added columns; the backend falls back to the same
--    defaults in routes/drivers.py, so behavior reverts to 7d/300s/all.)
--   ALTER TABLE service_areas DROP COLUMN IF EXISTS heatmap_data_window_hours;
--   ALTER TABLE service_areas DROP COLUMN IF EXISTS heatmap_refresh_seconds;
--   ALTER TABLE service_areas DROP COLUMN IF EXISTS heatmap_data_source;

ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS heatmap_data_window_hours integer NOT NULL DEFAULT 168;

ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS heatmap_refresh_seconds integer NOT NULL DEFAULT 300;

ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS heatmap_data_source text NOT NULL DEFAULT 'all_requests';

-- Constraints added separately (and idempotently) so re-running this file is
-- safe if a partial apply ever happened.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'service_areas_heatmap_window_check'
    ) THEN
        ALTER TABLE service_areas
            ADD CONSTRAINT service_areas_heatmap_window_check
            CHECK (heatmap_data_window_hours BETWEEN 1 AND 720);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'service_areas_heatmap_refresh_check'
    ) THEN
        ALTER TABLE service_areas
            ADD CONSTRAINT service_areas_heatmap_refresh_check
            CHECK (heatmap_refresh_seconds BETWEEN 30 AND 3600);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'service_areas_heatmap_source_check'
    ) THEN
        ALTER TABLE service_areas
            ADD CONSTRAINT service_areas_heatmap_source_check
            CHECK (heatmap_data_source IN ('all_requests', 'completed_rides', 'missed_rides'));
    END IF;
END $$;
