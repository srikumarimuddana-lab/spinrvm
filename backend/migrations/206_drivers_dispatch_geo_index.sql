-- Migration 206: partial (lat, lng) index for the geo-bounded dispatch candidate query
--
-- Context: match_driver_to_ride (routes/rides.py) now adds a lat/lng
-- bounding-box filter to its drivers candidate fetch, so dispatch no longer
-- pulls an arbitrary un-geo-filtered LIMIT 500 of every online driver (the
-- nearest driver could sit in row 501 → false "no drivers"). New query
-- pattern: WHERE is_online AND is_available AND ... AND lat BETWEEN a AND b
-- AND lng BETWEEN c AND d — this index serves it. Partial on the two flags
-- because dispatch only ever reads available drivers, keeping the index tiny
-- and cheap to maintain under the high-frequency location-update write path.
--
-- Forward-compatible: additive index on a small table (thousands of rows at
-- most); plain CREATE INDEX lock is momentary, well inside the 30s window.
--
-- Rollback: DROP INDEX IF EXISTS idx_drivers_dispatch_geo;

CREATE INDEX IF NOT EXISTS idx_drivers_dispatch_geo
    ON drivers (lat, lng)
    WHERE is_online = true AND is_available = true;
