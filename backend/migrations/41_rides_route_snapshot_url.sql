-- Migration 41: route snapshot URL on rides
--
-- complete_ride now renders a PNG snapshot of the ride path from the
-- phase_polylines column (migration 39) and uploads it to Cloudinary
-- so we have a permanent, CDN-backed image URL. This URL:
--   * lets the admin ride drawer show an <img> instead of re-rendering
--     the MapLibre map on every open
--   * embeds cleanly in the rider's email receipt (HTML <img>)
--
-- Backfill is intentionally *not* done here — the rendering depends on
-- the Python process being able to reach OSM tile servers, which is a
-- runtime concern, not a DB one. A one-shot admin job can populate
-- historical rides later if desired.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS route_snapshot_url TEXT;

COMMENT ON COLUMN rides.route_snapshot_url IS
    'Cloudinary URL of the rendered route PNG (pickup/dropoff markers + phase_polylines overlay). Populated asynchronously at ride completion.';
