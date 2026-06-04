-- 133_rides_pickup_nav_coords.sql
-- Adds a separate "navigation" pickup coordinate, snapped to the nearest
-- drivable road at booking. Riders can drop the pickup pin inside a mall /
-- building where no car can stop; pickup_lat/pickup_lng keep the rider's exact
-- pin (for display + the address they chose), while pickup_nav_lat/lng hold the
-- road-reachable point the driver navigates to and dispatch matches against.
-- NULL when snapping was unavailable or the pin was already on a road — readers
-- fall back to pickup_lat/pickup_lng.
--
-- Forward-compatible: additive nullable columns; existing rows + older backend
-- code are unaffected (they keep using pickup_lat/lng).
--
-- Rollback:
--   ALTER TABLE rides DROP COLUMN IF EXISTS pickup_nav_lat;
--   ALTER TABLE rides DROP COLUMN IF EXISTS pickup_nav_lng;

ALTER TABLE rides ADD COLUMN IF NOT EXISTS pickup_nav_lat DOUBLE PRECISION;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS pickup_nav_lng DOUBLE PRECISION;

COMMENT ON COLUMN rides.pickup_nav_lat IS
    'Pickup latitude snapped to the nearest drivable road at booking (route_distance.snap_to_road). NULL = use pickup_lat. Display/fare still use pickup_lat/lng.';
COMMENT ON COLUMN rides.pickup_nav_lng IS
    'Pickup longitude snapped to the nearest drivable road at booking. NULL = use pickup_lng.';
