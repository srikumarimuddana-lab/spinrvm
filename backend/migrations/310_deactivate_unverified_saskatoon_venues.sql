-- 310_deactivate_unverified_saskatoon_venues.sql
-- Takes the 38 Saskatoon venues seeded by 307/308/309 back offline, and
-- corrects the one venue center that is provably wrong.
--
-- WHY THIS IS A SEPARATE MIGRATION, not an edit to 307/308/309:
-- those three are merged and may already be applied. The runner keys
-- schema_migrations on the full filename, so editing an applied migration
-- never re-runs it — the fix would silently do nothing. Append-only is not
-- just convention here, it is the only thing that actually works.
--
-- WHY THE VENUES MUST GO DARK:
-- their coordinates are not survey-grade. Centers came from public geo
-- databases where an entry existed and were estimated from a street address
-- otherwise; every one of the ~98 pickup points was hand-authored as a small
-- offset from its venue center, so none corresponds to a surveyed door.
-- /maps/pickup-points sends the driver to the pickup point's coordinate, so
-- an invented entrance sends the driver to the wrong door. Two consequences
-- were already demonstrable before this migration:
--
--   1. Saskatchewan Polytechnic sat at 52.12833,-106.66028 — about 22nd St,
--      ~1.9 km south of its 1130 Idylwyld Dr N address. Its own "33rd Street
--      entrance" was 1921 m from the geocoded FreshCo row at 302 33rd St W
--      in migration 308. That center also dropped a 250 m detection circle
--      on top of City Hall, the downtown bus terminal, and the bar cluster.
--   2. 15 pairs of detection circles overlap. /maps/pickup-points returns the
--      NEAREST CENTER among all active radius matches, so a rider outside The
--      Rook & Raven resolves to Delta Bessborough (67 m) rather than Downtown
--      Nightlife (105 m) and is offered the hotel's doors, not their pub.
--
-- NON-DESTRUCTIVE BY DESIGN: every statement is guarded on
-- updated_at <= created_at + 5s, i.e. the row has not been touched since the
-- seed inserted it. routes/admin/venues.py stamps updated_at on every edit, so
-- a venue an admin has already corrected and deliberately activated is left
-- exactly as they left it. Re-running is a no-op for the same reason.
--
-- The 4 venues from migration 135 (Regina Airport, Cornwall Centre, Saskatoon
-- Airport, Midtown Plaza) are NOT touched — they predate this seed and are
-- intentionally live.
--
-- Before activating any of these, run:
--     python backend/scripts/geocode_seed_venues.py --apply
-- then verify on the map in Dashboard -> Pickup Venues and activate with
--     python backend/scripts/geocode_seed_venues.py --activate "<name>"
-- which refuses to activate a venue overlapping an already-active one.
--
-- Rollback (restores the pre-migration state exactly):
--   UPDATE venues SET is_active = true WHERE name IN (<the list below>);
--   UPDATE venues SET center_lat = 52.12833, center_lng = -106.66028,
--          pickup_points = '[{"name":"Main entrance (Idylwyld Dr)","lat":52.12860,"lng":-106.66000},
--                            {"name":"33rd Street entrance","lat":52.12900,"lng":-106.66100}]'::jsonb
--    WHERE name = 'Saskatchewan Polytechnic (Saskatoon)';

-- 1. Take the unverified seed offline. /maps/pickup-points filters on
--    is_active, so this alone removes them from every rider's app.
UPDATE venues
   SET is_active = false,
       updated_at = now()
 WHERE is_active = true
   AND updated_at <= created_at + interval '5 seconds'
   AND name IN (
        'The Centre (8th Street)',
        'Confederation Mall',
        'Lawson Heights Mall',
        'Market Mall (Saskatoon)',
        'Preston Crossing',
        'Blairmore Centre',
        'Brighton Marketplace',
        'Royal University Hospital',
        'Jim Pattison Children''s Hospital',
        'St. Paul''s Hospital',
        'Saskatoon City Hospital',
        'University of Saskatchewan',
        'Saskatchewan Polytechnic (Saskatoon)',
        'SaskTel Centre',
        'TCU Place',
        'Remai Modern',
        'Delta Bessborough Hotel',
        'Saskatoon City Hall',
        'Saskatoon Downtown Bus Terminal',
        'Costco (Marquis Drive)',
        'Walmart Supercentre (South Saskatoon)',
        'Real Canadian Superstore (Confederation Dr)',
        'Real Canadian Superstore (8th Street)',
        'Sobeys (Stonebridge)',
        'FreshCo (33rd Street)',
        'No Frills (Assiniboine Dr)',
        'Saskatoon Co-op (33rd Street)',
        'Walmart Supercentre (West Saskatoon)',
        'Costco (Market Drive)',
        'Giant Tiger (Circle Drive)',
        'Giant Tiger (Avenue F South)',
        'Home Depot (Circle Drive)',
        'Home Depot (South Saskatoon)',
        'Winners (8th Street)',
        'Marshalls (Meadows Parkway)',
        'Rona+ (West Saskatoon)',
        'Broadway District (Nutana)',
        'Downtown Nightlife (2nd Avenue)'
   );

-- 2. Correct the Polytechnic center and drop its contradictory entrance.
--    Still an estimate off the street grid (33rd St W is at 52.1444, per the
--    geocoded FreshCo row), not survey-grade — geocode_seed_venues.py replaces
--    it with a real geocode. Corrected here anyway so a wrong row is not left
--    sitting in the table waiting to be activated by hand.
UPDATE venues
   SET center_lat = 52.14000,
       center_lng = -106.66200,
       pickup_points = '[{"name":"Main entrance (Idylwyld Dr N)","lat":52.14020,"lng":-106.66170}]'::jsonb,
       updated_at = now()
 WHERE name = 'Saskatchewan Polytechnic (Saskatoon)'
   AND updated_at <= created_at + interval '5 seconds';
