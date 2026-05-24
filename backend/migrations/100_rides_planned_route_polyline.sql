-- Migration 100: store planned route polyline at ride creation
-- Rollback: ALTER TABLE rides DROP COLUMN IF EXISTS planned_route_polyline;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS planned_route_polyline JSONB DEFAULT '[]'::JSONB;

COMMENT ON COLUMN rides.planned_route_polyline IS
    'Google Directions API polyline captured at ride creation. [[lat,lng], ...] format. Used for route display on all surfaces without repeat API calls. Distinct from route_polyline which stores actual GPS breadcrumbs at completion.';
