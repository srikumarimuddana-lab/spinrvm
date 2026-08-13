-- 309_seed_saskatoon_retail_nightlife.sql
-- Additional Saskatoon pickup venues: TJX/retail stores and nightlife
-- districts. Bars in Broadway/Nutana and downtown are grouped into
-- district venues (single detection zone, individual bars as pickup
-- points) so a rider near any bar in the cluster gets a chooser listing
-- every nearby spot — much better than overlapping single-bar venues.
--
-- HomeSense (1723 Preston Ave N) and Rona+ (1722 Preston Ave N) are
-- intentionally omitted — they sit inside Preston Crossing's 350 m
-- detection radius and riders there already get that venue's chooser.
--
-- Idempotent: INSERT … WHERE NOT EXISTS, same pattern as 135/307/308.
--
-- IMPORTANT: coordinates are approximate. An admin should fine-tune via
-- Dashboard → Pickup Venues before relying on exact entrance positions.
--
-- Rollback:
--   DELETE FROM venues WHERE name IN (
--     'Winners (8th Street)',
--     'Marshalls (Meadows Parkway)',
--     'Rona+ (West Saskatoon)',
--     'Broadway District (Nutana)',
--     'Downtown Nightlife (2nd Avenue)'
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
  -- RETAIL STORES
  -- ──────────────────────────────────────────────────────────────

  -- Winners — 2319 8th St E (between Preston Ave & Arlington Ave)
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Winners (8th Street)', 52.11100, -106.61500, 150,
      '[{"name":"Main entrance (8th St)","lat":52.11120,"lng":-106.61470},
        {"name":"Parking lot (south side)","lat":52.11070,"lng":-106.61540}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Winners (8th Street)');

  -- Marshalls — 3020 Meadows Pkwy (Rosewood / southeast)
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Marshalls (Meadows Parkway)', 52.08700, -106.54500, 180,
      '[{"name":"Main entrance (Meadows Pkwy)","lat":52.08720,"lng":-106.54470},
        {"name":"East parking lot","lat":52.08680,"lng":-106.54550}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Marshalls (Meadows Parkway)');

  -- Rona+ West Saskatoon — 125 Betts Ave (near Walmart West)
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Rona+ (West Saskatoon)', 52.11550, -106.73400, 180,
      '[{"name":"Main entrance (Betts Ave)","lat":52.11570,"lng":-106.73370},
        {"name":"Contractor / lumber pickup","lat":52.11520,"lng":-106.73450}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Rona+ (West Saskatoon)');

  -- ──────────────────────────────────────────────────────────────
  -- NIGHTLIFE DISTRICTS
  -- ──────────────────────────────────────────────────────────────
  -- Bars in a cluster share one venue so the rider gets a chooser
  -- listing every nearby spot instead of a random single-bar match.

  -- Broadway District (Nutana) — Broadway Ave between 8th & 12th St
  -- Covers: Buds on Broadway, Yard & Flagon, Amigos Cantina, Leopold's Tavern
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Broadway District (Nutana)', 52.11750, -106.65350, 250,
      '[{"name":"Buds on Broadway (817 Broadway Ave)","lat":52.11760,"lng":-106.65300},
        {"name":"Yard & Flagon (718 Broadway Ave)","lat":52.11720,"lng":-106.65340},
        {"name":"Amigos Cantina (806 Dufferin Ave)","lat":52.11756,"lng":-106.65445},
        {"name":"Leopold''s Tavern (616 10th St E)","lat":52.11740,"lng":-106.65250}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Broadway District (Nutana)');

  -- Downtown Nightlife (2nd Avenue) — 1st–3rd Ave, 20th–22nd St
  -- Covers: Capitol Music Club, Diva's Nightclub, Rook & Raven, Winston's Pub
  INSERT INTO venues (name, center_lat, center_lng, radius_m, pickup_points, service_area_id, is_active)
  SELECT 'Downtown Nightlife (2nd Avenue)', 52.12600, -106.66050, 250,
      '[{"name":"Capitol Music Club (244 1st Ave N)","lat":52.12650,"lng":-106.66100},
        {"name":"Diva''s Nightclub (220 3rd Ave S)","lat":52.12500,"lng":-106.66000},
        {"name":"The Rook & Raven (154 2nd Ave S)","lat":52.12580,"lng":-106.65900},
        {"name":"Winston''s Pub (243 21st St E)","lat":52.12700,"lng":-106.65800}]'::jsonb,
      _sa_id, true
  WHERE NOT EXISTS (SELECT 1 FROM venues WHERE name = 'Downtown Nightlife (2nd Avenue)');

END $$;
