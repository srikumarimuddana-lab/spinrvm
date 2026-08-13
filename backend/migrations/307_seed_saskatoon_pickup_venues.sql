-- 307_seed_saskatoon_pickup_venues.sql
-- Comprehensive Saskatoon pickup venues: malls, hospitals, university,
-- landmarks, transit hubs, and major stores. Gives riders curated meeting
-- points at high-traffic locations so the driver knows exactly which door
-- to pull up to.
--
-- Idempotent: each venue is inserted only if a venue with the same name
-- doesn't already exist, so re-running is a no-op and an admin's later
-- edits are never overwritten.
--
-- IMPORTANT: coordinates are sourced from Wikipedia / public geo databases
-- and are approximate. An admin should open Dashboard → Pickup Venues and
-- fine-tune each detection center, radius, and entrance coordinate on the
-- map before relying on them — a wrong entrance coordinate sends the
-- driver to the wrong door.
--
-- The Saskatoon service_area_id (361d17bb-ec55-4561-943f-e3bbee5d7a55) is
-- set when the row exists; if it doesn't, service_area_id stays NULL and
-- the venue still works (detection is coordinate-based, not area-based).
--
-- Rollback:
--   DELETE FROM venues WHERE name IN (
--     'The Centre (8th Street)',
--     'Confederation Mall',
--     'Lawson Heights Mall',
--     'Market Mall (Saskatoon)',
--     'Preston Crossing',
--     'Blairmore Centre',
--     'Brighton Marketplace',
--     'Royal University Hospital',
--     'Jim Pattison Children''s Hospital',
--     'St. Paul''s Hospital',
--     'Saskatoon City Hospital',
--     'University of Saskatchewan',
--     'Saskatchewan Polytechnic (Saskatoon)',
--     'SaskTel Centre',
--     'TCU Place',
--     'Remai Modern',
--     'Delta Bessborough Hotel',
--     'Saskatoon Downtown Bus Terminal',
--     'Costco (Marquis Drive)',
--     'Walmart Supercentre (South Saskatoon)',
--     'Saskatoon City Hall'
--   );

-- Helper: resolve Saskatoon service_area_id if the row exists.
DO $$
DECLARE
  _sa_id text;
BEGIN
  SELECT id::text INTO _sa_id
    FROM service_areas
   WHERE id::text = '361d17bb-ec55-4561-943f-e3bbee5d7a55'
   LIMIT 1;

  -- ──────────────────────────────────────────────────────────────
  -- MALLS & SHOPPING CENTRES
  -- ──────────────────────────────────────────────────────────────

  -- The Centre (8th Street) — 3510 8th St E
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'The Centre (8th Street)', 52.11333, -106.59889, 300,
      '[{"name":"8th Street main entrance","lat":52.11360,"lng":-106.59850},
        {"name":"East parking lot (near Canadian Tire)","lat":52.11280,"lng":-106.59680},
        {"name":"West entrance (Clarence Ave side)","lat":52.11350,"lng":-106.60100}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'The Centre (8th Street)');

  -- Confederation Mall — 3440 8th St W / Confederation Dr
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Confederation Mall', 52.13250, -106.72139, 250,
      '[{"name":"Main entrance (Confederation Dr)","lat":52.13280,"lng":-106.72100},
        {"name":"South entrance (near Sobeys)","lat":52.13180,"lng":-106.72200},
        {"name":"North parking lot","lat":52.13350,"lng":-106.72050}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Confederation Mall');

  -- Lawson Heights Mall — 134 Primrose Dr
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Lawson Heights Mall', 52.16833, -106.65639, 200,
      '[{"name":"Main entrance (Primrose Dr)","lat":52.16860,"lng":-106.65600},
        {"name":"South entrance","lat":52.16780,"lng":-106.65680}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Lawson Heights Mall');

  -- Market Mall — 2325 Preston Ave S
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Market Mall (Saskatoon)', 52.10129, -106.62005, 250,
      '[{"name":"Preston Ave entrance","lat":52.10170,"lng":-106.62050},
        {"name":"Grosvenor Park entrance","lat":52.10080,"lng":-106.61920},
        {"name":"North lot (near food court)","lat":52.10200,"lng":-106.61950}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Market Mall (Saskatoon)');

  -- Preston Crossing — Preston Ave N & Circle Dr
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Preston Crossing', 52.14883, -106.61854, 350,
      '[{"name":"Main entrance (Preston Ave)","lat":52.14920,"lng":-106.61900},
        {"name":"Cineplex entrance","lat":52.14850,"lng":-106.61750},
        {"name":"North lot (near Chapters)","lat":52.14980,"lng":-106.61800}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Preston Crossing');

  -- Blairmore Centre — Boychuk Dr area
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Blairmore Centre', 52.09700, -106.59200, 300,
      '[{"name":"Main entrance (Boychuk Dr)","lat":52.09730,"lng":-106.59150},
        {"name":"South entrance","lat":52.09650,"lng":-106.59250}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Blairmore Centre');

  -- Brighton Marketplace — 155 Gibson Bend
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Brighton Marketplace', 52.12400, -106.55400, 250,
      '[{"name":"Main entrance (Gibson Bend)","lat":52.12430,"lng":-106.55370},
        {"name":"McOrmond Dr entrance","lat":52.12370,"lng":-106.55500}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Brighton Marketplace');

  -- ──────────────────────────────────────────────────────────────
  -- HOSPITALS
  -- ──────────────────────────────────────────────────────────────

  -- Royal University Hospital — 103 Hospital Dr
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Royal University Hospital', 52.13069, -106.64080, 300,
      '[{"name":"Main entrance (Hospital Dr)","lat":52.13100,"lng":-106.64050},
        {"name":"Emergency department entrance","lat":52.13020,"lng":-106.64120},
        {"name":"East wing entrance","lat":52.13050,"lng":-106.63900}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Royal University Hospital');

  -- Jim Pattison Children's Hospital — adjacent to RUH campus
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Jim Pattison Children''s Hospital', 52.13194, -106.64250, 200,
      '[{"name":"Main entrance","lat":52.13220,"lng":-106.64220},
        {"name":"Emergency entrance","lat":52.13160,"lng":-106.64300}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Jim Pattison Children''s Hospital');

  -- St. Paul's Hospital — 1702 20th St W
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'St. Paul''s Hospital', 52.12707, -106.69612, 250,
      '[{"name":"Main entrance (20th St)","lat":52.12740,"lng":-106.69580},
        {"name":"Emergency entrance","lat":52.12670,"lng":-106.69650},
        {"name":"Avenue P entrance","lat":52.12750,"lng":-106.69700}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'St. Paul''s Hospital');

  -- Saskatoon City Hospital — 701 Queen St
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Saskatoon City Hospital', 52.13574, -106.65394, 250,
      '[{"name":"Main entrance (Queen St)","lat":52.13600,"lng":-106.65360},
        {"name":"South entrance","lat":52.13530,"lng":-106.65430}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Saskatoon City Hospital');

  -- ──────────────────────────────────────────────────────────────
  -- UNIVERSITIES & EDUCATION
  -- ──────────────────────────────────────────────────────────────

  -- University of Saskatchewan — Place Riel / Campus Dr
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'University of Saskatchewan', 52.13285, -106.63140, 500,
      '[{"name":"Place Riel (Campus Dr)","lat":52.13310,"lng":-106.63100},
        {"name":"College Dr entrance (Bowl)","lat":52.13050,"lng":-106.63400},
        {"name":"Arts Building (Cumberland Ave)","lat":52.13200,"lng":-106.62800},
        {"name":"PAC / Physical Activity Complex","lat":52.13400,"lng":-106.63300}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'University of Saskatchewan');

  -- Saskatchewan Polytechnic — 1130 Idylwyld Dr N
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Saskatchewan Polytechnic (Saskatoon)', 52.12833, -106.66028, 250,
      '[{"name":"Main entrance (Idylwyld Dr)","lat":52.12860,"lng":-106.66000},
        {"name":"33rd Street entrance","lat":52.12900,"lng":-106.66100}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Saskatchewan Polytechnic (Saskatoon)');

  -- ──────────────────────────────────────────────────────────────
  -- LANDMARKS, ARENAS & CONVENTION CENTRES
  -- ──────────────────────────────────────────────────────────────

  -- SaskTel Centre — 3515 Thatcher Ave (arena)
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'SaskTel Centre', 52.18900, -106.67900, 400,
      '[{"name":"Gate 1 (main entrance)","lat":52.18930,"lng":-106.67850},
        {"name":"Gate 4 (south side)","lat":52.18850,"lng":-106.67950},
        {"name":"East parking lot (Thatcher Ave)","lat":52.18920,"lng":-106.67700}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'SaskTel Centre');

  -- TCU Place (Convention Centre) — 35 22nd St E
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'TCU Place', 52.12861, -106.66778, 150,
      '[{"name":"Main entrance (22nd St)","lat":52.12880,"lng":-106.66750},
        {"name":"Spadina Crescent entrance","lat":52.12830,"lng":-106.66820}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'TCU Place');

  -- Remai Modern Art Gallery — 102 Spadina Crescent E
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Remai Modern', 52.12290, -106.66740, 150,
      '[{"name":"Main entrance (Spadina Crescent)","lat":52.12310,"lng":-106.66710},
        {"name":"River Landing side","lat":52.12260,"lng":-106.66780}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Remai Modern');

  -- Delta Bessborough Hotel — 601 Spadina Crescent E
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Delta Bessborough Hotel', 52.12639, -106.65917, 150,
      '[{"name":"Main entrance (Spadina Crescent)","lat":52.12660,"lng":-106.65890},
        {"name":"21st Street entrance","lat":52.12610,"lng":-106.65950}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Delta Bessborough Hotel');

  -- Saskatoon City Hall — 222 3rd Ave N
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Saskatoon City Hall', 52.13083, -106.66100, 120,
      '[{"name":"Main entrance (3rd Ave)","lat":52.13100,"lng":-106.66070},
        {"name":"23rd Street side","lat":52.13060,"lng":-106.66130}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Saskatoon City Hall');

  -- ──────────────────────────────────────────────────────────────
  -- TRANSIT
  -- ──────────────────────────────────────────────────────────────

  -- Saskatoon Downtown Bus Terminal — 23rd St between 2nd & 3rd Ave
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Saskatoon Downtown Bus Terminal', 52.13028, -106.66222, 100,
      '[{"name":"2nd Avenue side","lat":52.13040,"lng":-106.66180},
        {"name":"3rd Avenue side","lat":52.13020,"lng":-106.66260}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Saskatoon Downtown Bus Terminal');

  -- ──────────────────────────────────────────────────────────────
  -- SUPERMARKETS & BIG BOX
  -- ──────────────────────────────────────────────────────────────

  -- Costco (Marquis Drive) — 115 Marquis Dr W
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Costco (Marquis Drive)', 52.19385, -106.67609, 200,
      '[{"name":"Main store entrance","lat":52.19400,"lng":-106.67570},
        {"name":"Gas bar area","lat":52.19340,"lng":-106.67700}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Costco (Marquis Drive)');

  -- Walmart Supercentre (South Saskatoon) — 3035 Clarence Ave S
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Walmart Supercentre (South Saskatoon)', 52.10050, -106.63500, 200,
      '[{"name":"Main entrance (Clarence Ave)","lat":52.10080,"lng":-106.63470},
        {"name":"Garden centre entrance","lat":52.10010,"lng":-106.63550}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Walmart Supercentre (South Saskatoon)');

END $$;
