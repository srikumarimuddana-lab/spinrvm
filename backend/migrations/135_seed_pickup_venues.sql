-- 135_seed_pickup_venues.sql
-- Starter pickup venues for Regina + Saskatoon so the curated-pickup feature
-- works out of the box. Idempotent: each venue is inserted only if a venue with
-- the same name doesn't already exist, so re-running is a no-op and an admin's
-- later edits are never overwritten.
--
-- IMPORTANT: the coordinates below are APPROXIMATE starting points. An admin
-- should open Dashboard → Pickup Venues and fine-tune each detection center,
-- radius, and entrance coordinate on the map before relying on them — a wrong
-- entrance coordinate would send the driver to the wrong door.
--
-- Rollback:
--   DELETE FROM venues WHERE name IN (
--     'Regina International Airport', 'Cornwall Centre (Regina)',
--     'Saskatoon John G. Diefenbaker Int''l Airport', 'Midtown Plaza (Saskatoon)');

INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, is_active)
SELECT 'Regina International Airport', 50.43260, -104.66690, 600,
    '[{"name":"Arrivals (ground level)","lat":50.43210,"lng":-104.66650},
      {"name":"Departures (upper level)","lat":50.43290,"lng":-104.66720}]'::jsonb,
    true
WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Regina International Airport');

INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, is_active)
SELECT 'Cornwall Centre (Regina)', 50.44860, -104.61260, 250,
    '[{"name":"11th Avenue entrance","lat":50.44900,"lng":-104.61300},
      {"name":"Saskatchewan Drive entrance","lat":50.44800,"lng":-104.61220}]'::jsonb,
    true
WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Cornwall Centre (Regina)');

INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, is_active)
SELECT 'Saskatoon John G. Diefenbaker Int''l Airport', 52.16900, -106.68900, 600,
    '[{"name":"Arrivals curb","lat":52.16880,"lng":-106.68850},
      {"name":"Departures curb","lat":52.16930,"lng":-106.68950}]'::jsonb,
    true
WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Saskatoon John G. Diefenbaker Int''l Airport');

INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, is_active)
SELECT 'Midtown Plaza (Saskatoon)', 52.12300, -106.65850, 250,
    '[{"name":"1st Avenue entrance","lat":52.12330,"lng":-106.65800},
      {"name":"2nd Avenue entrance","lat":52.12270,"lng":-106.65920},
      {"name":"23rd Street entrance","lat":52.12250,"lng":-106.65860}]'::jsonb,
    true
WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Midtown Plaza (Saskatoon)');
