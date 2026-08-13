-- 308_seed_saskatoon_stores.sql
-- Additional Saskatoon pickup venues: supermarkets, grocery stores, and big-box
-- retailers that sit OUTSIDE existing venue radii from migrations 135/307.
-- Stores already inside an existing venue's detection radius (e.g. Canadian Tire
-- inside Preston Crossing, Co-op inside The Centre) are intentionally omitted —
-- those riders already get the parent venue's pickup-point chooser.
--
-- Idempotent: INSERT … WHERE NOT EXISTS, same pattern as 135/307.
--
-- IMPORTANT: coordinates are approximate. An admin should fine-tune via
-- Dashboard → Pickup Venues before relying on exact entrance positions.
--
-- Rollback:
--   DELETE FROM venues WHERE name IN (
--     'Real Canadian Superstore (Confederation Dr)',
--     'Real Canadian Superstore (8th Street)',
--     'Walmart Supercentre (West Saskatoon)',
--     'Costco (Market Drive)',
--     'Giant Tiger (Circle Drive)',
--     'Giant Tiger (Avenue F South)',
--     'Sobeys (Stonebridge)',
--     'FreshCo (33rd Street)',
--     'No Frills (Assiniboine Dr)',
--     'Home Depot (Circle Drive)',
--     'Home Depot (South Saskatoon)',
--     'Saskatoon Co-op (33rd Street)'
--   );

DO $$
DECLARE
  _sa_id text;
BEGIN
  SELECT id::text INTO _sa_id
    FROM service_areas
   WHERE id::text = '361d17bb-ec55-4561-943f-e3bbee5d7a55'
   LIMIT 1;

  -- ──────────────────────────────────────────────────────────────
  -- SUPERMARKETS / GROCERY
  -- ──────────────────────────────────────────────────────────────

  -- Real Canadian Superstore — 411 Confederation Dr
  -- (~290 m from Confederation Mall center, outside its 250 m radius)
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Real Canadian Superstore (Confederation Dr)', 52.13229, -106.72563, 200,
      '[{"name":"Main entrance (Confederation Dr)","lat":52.13260,"lng":-106.72530},
        {"name":"Pickup parking (north side)","lat":52.13280,"lng":-106.72600}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Real Canadian Superstore (Confederation Dr)');

  -- Real Canadian Superstore — 2901 8th St E
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Real Canadian Superstore (8th Street)', 52.11200, -106.60700, 200,
      '[{"name":"Main entrance (8th St)","lat":52.11230,"lng":-106.60670},
        {"name":"Click & collect / pickup lane","lat":52.11170,"lng":-106.60750}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Real Canadian Superstore (8th Street)');

  -- Sobeys Stonebridge — 3100 Preston Ave S
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Sobeys (Stonebridge)', 52.09400, -106.62800, 180,
      '[{"name":"Main entrance (Preston Ave)","lat":52.09430,"lng":-106.62770},
        {"name":"Pharmacy entrance","lat":52.09370,"lng":-106.62830}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Sobeys (Stonebridge)');

  -- FreshCo — 302 33rd St W
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'FreshCo (33rd Street)', 52.14438, -106.67382, 150,
      '[{"name":"Main entrance (33rd St)","lat":52.14460,"lng":-106.67350},
        {"name":"Parking lot entrance (Avenue C)","lat":52.14410,"lng":-106.67420}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'FreshCo (33rd Street)');

  -- No Frills — 7 Assiniboine Dr
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'No Frills (Assiniboine Dr)', 52.15500, -106.59500, 150,
      '[{"name":"Main entrance","lat":52.15520,"lng":-106.59470},
        {"name":"Parking lot (Assiniboine Dr)","lat":52.15480,"lng":-106.59540}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'No Frills (Assiniboine Dr)');

  -- Saskatoon Co-op — 1624 33rd St W
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Saskatoon Co-op (33rd Street)', 52.14400, -106.68400, 150,
      '[{"name":"Main entrance (33rd St)","lat":52.14420,"lng":-106.68370},
        {"name":"Gas bar entrance","lat":52.14380,"lng":-106.68440}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Saskatoon Co-op (33rd Street)');

  -- ──────────────────────────────────────────────────────────────
  -- WALMART (additional locations)
  -- ──────────────────────────────────────────────────────────────

  -- Walmart Supercentre West — 225 Betts Ave (Hwy 7 & 14 area)
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Walmart Supercentre (West Saskatoon)', 52.11500, -106.73500, 200,
      '[{"name":"Main entrance (Betts Ave)","lat":52.11530,"lng":-106.73460},
        {"name":"Garden centre entrance","lat":52.11460,"lng":-106.73550}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Walmart Supercentre (West Saskatoon)');

  -- ──────────────────────────────────────────────────────────────
  -- COSTCO (second location)
  -- ──────────────────────────────────────────────────────────────

  -- Costco — 225 Market Dr (east Saskatoon / Lakewood)
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Costco (Market Drive)', 52.09124, -106.53327, 200,
      '[{"name":"Main store entrance","lat":52.09150,"lng":-106.53290},
        {"name":"Gas bar area","lat":52.09090,"lng":-106.53380}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Costco (Market Drive)');

  -- ──────────────────────────────────────────────────────────────
  -- GIANT TIGER
  -- ──────────────────────────────────────────────────────────────

  -- Giant Tiger — 810 Circle Dr E
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Giant Tiger (Circle Drive)', 52.14000, -106.60000, 150,
      '[{"name":"Main entrance (Circle Dr)","lat":52.14020,"lng":-106.59970},
        {"name":"Side entrance","lat":52.13980,"lng":-106.60040}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Giant Tiger (Circle Drive)');

  -- Giant Tiger — 105 Ave F South (Pleasant Hill)
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Giant Tiger (Avenue F South)', 52.12000, -106.68500, 150,
      '[{"name":"Main entrance (Ave F)","lat":52.12020,"lng":-106.68470},
        {"name":"20th Street side","lat":52.11980,"lng":-106.68540}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Giant Tiger (Avenue F South)');

  -- ──────────────────────────────────────────────────────────────
  -- HOME IMPROVEMENT
  -- ──────────────────────────────────────────────────────────────

  -- Home Depot — 707 Circle Dr E
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Home Depot (Circle Drive)', 52.13800, -106.60000, 200,
      '[{"name":"Main entrance (Circle Dr)","lat":52.13830,"lng":-106.59960},
        {"name":"Contractor / lumber pickup","lat":52.13760,"lng":-106.60050}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Home Depot (Circle Drive)');

  -- Home Depot — 3043 Clarence Ave S (Stonebridge)
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Home Depot (South Saskatoon)', 52.10100, -106.63300, 200,
      '[{"name":"Main entrance (Clarence Ave)","lat":52.10130,"lng":-106.63270},
        {"name":"Contractor / lumber pickup","lat":52.10060,"lng":-106.63350}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Home Depot (South Saskatoon)');

END $$;
