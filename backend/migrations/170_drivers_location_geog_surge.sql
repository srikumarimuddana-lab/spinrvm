-- 170_drivers_location_geog_surge.sql
--
-- Purpose (ACTION_ITEMS D1): server-side spatial supply counting for the surge
-- engine. Replaces the Python point-in-polygon over a capped driver fetch
-- (utils/surge_engine.py) — which truncated supply past the row cap and could
-- mis-price a regulated fare — with a PostGIS ST_Covers lookup backed by a
-- partial GIST index (no cap, index-only). The function returns the available
-- drivers inside a polygon; the surge engine still applies the Redis presence
-- filter in Python, so ghost-online drivers are excluded exactly as before.
--
-- COORDINATED DEPLOY: the surge engine reads this path behind the
-- SURGE_SPATIAL_COUNT flag (default OFF) and falls back to the Python scan on
-- any error, so applying this migration changes nothing until the flag is
-- enabled after a staging rehearsal (run EXPLAIN ANALYZE against a real polygon
-- to confirm the GIST index is used, and verify driver-id type parity with
-- utils/driver_presence.present_driver_ids).
--
-- FORWARD-COMPATIBLE / lock-safety:
--   * location_geog is added NULLABLE → instant metadata-only add, NO rewrite
--     (do NOT use a GENERATED STORED column — that rewrites under ACCESS EXCLUSIVE).
--   * a BEFORE INSERT/UPDATE-OF-(lat,lng) trigger keeps it in sync going forward.
--   * existing rows are backfilled in 1000-row batches.
--   * the GIST index is built CONCURRENTLY (drivers is a high-traffic table); the
--     migrate runner detects CONCURRENTLY and applies the whole file in autocommit.
--   * both functions pin search_path = public, extensions (PostGIS lives in the
--     `extensions` schema on Supabase) so they resolve regardless of session state.
--   * drivers_available_in_polygon is REVOKEd from PUBLIC/anon/authenticated and
--     GRANTed only to service_role (it returns driver-location-derived data, so a
--     default PUBLIC EXECUTE on a SECURITY DEFINER fn would be a PIPEDA leak).
--
-- Rollback:
--   DROP FUNCTION IF EXISTS drivers_available_in_polygon(text);
--   DROP TRIGGER IF EXISTS trg_drivers_location_geog ON drivers;
--   DROP FUNCTION IF EXISTS _drivers_sync_location_geog();
--   DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_location_geog_available;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS location_geog;
--   -- (the postgis extension is left enabled; harmless.)

CREATE EXTENSION IF NOT EXISTS postgis SCHEMA extensions;

-- Resolve PostGIS symbols for the rest of this migration session (trigger
-- creation, backfill DO block). Per-function SET search_path below covers runtime.
SET search_path = public, extensions;

ALTER TABLE drivers ADD COLUMN IF NOT EXISTS location_geog geography(Point, 4326);

CREATE OR REPLACE FUNCTION _drivers_sync_location_geog() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
    IF NEW.lat IS NOT NULL AND NEW.lng IS NOT NULL THEN
        NEW.location_geog := ST_SetSRID(ST_MakePoint(NEW.lng, NEW.lat), 4326)::geography;
    ELSE
        NEW.location_geog := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_drivers_location_geog ON drivers;
CREATE TRIGGER trg_drivers_location_geog
    BEFORE INSERT OR UPDATE OF lat, lng ON drivers
    FOR EACH ROW EXECUTE FUNCTION _drivers_sync_location_geog();

-- Batched backfill of existing rows.
DO $$
DECLARE
    _rows integer;
BEGIN
    LOOP
        UPDATE drivers
        SET location_geog = ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography
        WHERE id IN (
            SELECT id FROM drivers
            WHERE location_geog IS NULL AND lat IS NOT NULL AND lng IS NOT NULL
            LIMIT 1000
        );
        GET DIAGNOSTICS _rows = ROW_COUNT;
        EXIT WHEN _rows = 0;
    END LOOP;
END;
$$;

-- drivers is a high-traffic table → build the index CONCURRENTLY so it never
-- takes an ACCESS EXCLUSIVE lock. The migrate runner detects CONCURRENTLY and
-- runs the whole file in autocommit (CONCURRENTLY cannot run in a transaction).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_location_geog_available
    ON drivers USING GIST (location_geog)
    WHERE is_online AND is_available;

-- Returns available drivers inside the polygon (presence applied in Python).
-- SECURITY DEFINER + pinned search_path; the caller builds GeoJSON as [lng, lat].
CREATE OR REPLACE FUNCTION drivers_available_in_polygon(p_polygon_geojson text)
RETURNS TABLE(driver_id text, driver_user_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, extensions AS $$
    SELECT d.id::text, d.user_id::text
    FROM drivers d
    WHERE d.is_online = true
      AND d.is_available = true
      AND d.location_geog IS NOT NULL
      AND ST_Covers(
          ST_SetSRID(ST_GeomFromGeoJSON(p_polygon_geojson), 4326)::geography,
          d.location_geog
      );
$$;

-- Backend-only: a SECURITY DEFINER fn returning driver-location-derived data must
-- not be callable by anon/authenticated keys (PIPEDA). Default CREATE grants
-- EXECUTE to PUBLIC — revoke it and grant only the service role.
REVOKE EXECUTE ON FUNCTION drivers_available_in_polygon(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION drivers_available_in_polygon(text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION drivers_available_in_polygon(text) TO service_role;
