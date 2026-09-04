-- 404_dispatch_candidate_pool_and_provider_default.sql
--
-- Rollback:
--   ALTER TABLE public.settings ALTER COLUMN dispatch_geo_provider SET DEFAULT 'legacy';
--   UPDATE public.settings SET dispatch_geo_provider = 'legacy' WHERE dispatch_geo_provider = 'postgis';
--   ALTER TABLE public.settings
--       DROP CONSTRAINT IF EXISTS settings_max_candidate_pool_chk;
--   ALTER TABLE public.service_areas
--       DROP CONSTRAINT IF EXISTS service_areas_max_candidate_pool_chk;
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS max_candidate_pool;
--   ALTER TABLE public.service_areas DROP COLUMN IF EXISTS max_candidate_pool;
--   -- NOTE: the UPDATE reversal is only correct while no operator has
--   -- deliberately chosen 'postgis' for their own reasons. It restores the
--   -- pre-404 state of the single production settings row, which held
--   -- 'legacy'. Per-area overrides (service_areas.dispatch_geo_provider) are
--   -- untouched by this migration in either direction.
--
-- Why this migration exists
-- ------------------------
-- Part 1 — `max_candidate_pool` becomes configurable.
--
-- backend/routes/rides/matching.py reads the dispatch candidate pool with a
-- hardcoded `limit=500` (two sites: the primary pool and the vehicle-cascade
-- pool). 500 is not a tuned number, it is a literal, and it is the same
-- literal for a 6-driver rural area as for Regina at surge. This adds the
-- knob with the same area-overrides-global precedence every other matching
-- field already uses (search_radius_km — migration 10b;
-- max_simultaneous_offers — migration 100; dispatch_geo_provider —
-- migration 397): `service_areas.max_candidate_pool` when set, otherwise
-- `settings.max_candidate_pool`, resolved in
-- DispatchService.resolve_matching_config().
--
-- The CHECK floor of 50 is deliberate, not decorative — see Part 2. The
-- ceiling of 500 preserves today's behaviour as the maximum: this migration
-- can only ever make the pool smaller than it is now, never larger, so no
-- existing dispatch read can get more expensive because of it.
--
-- Part 2 — the global geo provider default flips `legacy` -> `postgis`.
--
-- This is the part that actually matters, and it is why the pool cap could
-- not be shipped on its own.
--
-- `legacy` is the lat/lng bounding-box read in matching.py. It passes NO
-- `order` argument to get_rows (backend/repositories/_base.py — get_rows
-- accepts `order`; matching.py has simply never passed one). So the LIMIT is
-- an UNORDERED LIMIT: Postgres returns an ARBITRARY N rows from inside the
-- box, and distance ranking happens afterwards, in Python, over only those
-- rows. The nearest driver is therefore NOT guaranteed to be in the returned
-- set. Whenever the in-box candidate count exceeds the cap, the closest
-- driver can sit in row N+1 and dispatch reports a false "no drivers" — or,
-- worse, quietly offers the ride to a further driver while the nearest one
-- idles. matching.py warns about exactly this failure mode in its own
-- comments at the candidate-read site.
--
-- Today that bug is dormant only because 500 is high enough that the box
-- never truncates. Making the cap configurable (Part 1) is precisely what
-- would wake it up. Shipping a lowerable cap on top of an unordered LIMIT
-- would be shipping a silent nearest-driver-drop switch.
--
-- `postgis` does not have this problem. The `drivers_nearby_location_geog`
-- RPC (migration 398) runs ORDER BY ST_Distance against the trigger-
-- maintained `location_geog` geography column and its partial GiST index
-- (migration 170), i.e. a true nearest-N: the rows it drops are by
-- construction the FURTHEST ones, never the nearest. Under postgis a small
-- pool degrades pool depth, which is the intended tradeoff. Under legacy a
-- small pool degrades correctness, which is not.
--
-- Risk of the flip is bounded by code that already exists and is already
-- tested: `_postgis_or_fallback` in backend/services/dispatch_candidates.py
-- catches ANY exception from the RPC path — function missing, PostGIS
-- unavailable, timeout, malformed response — logs it, emits a failover
-- metric plus an admin-visible event, and transparently re-runs the legacy
-- bounding-box query for that dispatch. A bad postgis path degrades to
-- exactly today's behaviour, loudly, per dispatch. It does not strand rides.
--
-- Asymmetry note (migration 397, verified against the live schema): these
-- two columns are NOT symmetric.
--   settings.dispatch_geo_provider      TEXT NOT NULL DEFAULT 'legacy'  (global)
--   service_areas.dispatch_geo_provider TEXT NULL                       (optional override)
-- Every production service_areas row currently holds NULL, meaning "inherit
-- the global", and resolve_provider() in dispatch_candidates.py only honours
-- the area value when it is a valid non-null string. So there is deliberately
-- NO `UPDATE public.service_areas ... WHERE dispatch_geo_provider = 'legacy'`
-- below: it would match zero rows and imply a per-area opt-out that does not
-- exist. Updating the single `settings` row is what switches every area.
-- The ALTER ... SET DEFAULT covers a future settings row inserted from
-- scratch (fresh env, disaster recovery), which the UPDATE alone would not.
--
-- Additive only: two nullable-with-default columns and a default change. No
-- table rewrite — `settings` is a single-row config table, and
-- `service_areas` holds single-digit rows. The CHECK constraints are added
-- guarded by pg_constraint lookups, matching migration 397's pattern, so a
-- re-run is a no-op.

ALTER TABLE public.service_areas
    ADD COLUMN IF NOT EXISTS max_candidate_pool INT DEFAULT 500;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS max_candidate_pool INT DEFAULT 500;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'service_areas_max_candidate_pool_chk'
    ) THEN
        ALTER TABLE public.service_areas
            ADD CONSTRAINT service_areas_max_candidate_pool_chk CHECK (
                max_candidate_pool IS NULL
                OR max_candidate_pool BETWEEN 50 AND 500
            );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'settings_max_candidate_pool_chk'
    ) THEN
        ALTER TABLE public.settings
            ADD CONSTRAINT settings_max_candidate_pool_chk CHECK (
                max_candidate_pool IS NULL
                OR max_candidate_pool BETWEEN 50 AND 500
            );
    END IF;
END $$;

-- Flip the live global. Scoped to rows still on the migration-397 default so
-- an operator who has already chosen 'shadow' or 'h3' is not overwritten.
UPDATE public.settings
SET dispatch_geo_provider = 'postgis'
WHERE dispatch_geo_provider = 'legacy';

ALTER TABLE public.settings
    ALTER COLUMN dispatch_geo_provider SET DEFAULT 'postgis';

COMMENT ON COLUMN public.service_areas.max_candidate_pool IS
    'Per-area override for the dispatch candidate pool cap (50-500). NULL = '
    'inherit settings.max_candidate_pool. Resolved by '
    'DispatchService.resolve_matching_config(). Values below 200 combined '
    'with dispatch_geo_provider = ''legacy'' log a loud warning at dispatch '
    'time: legacy is an UNORDERED LIMIT and cannot guarantee the nearest '
    'driver is in the pool. See migration 404 header.';

COMMENT ON COLUMN public.settings.max_candidate_pool IS
    'Global dispatch candidate pool cap (50-500), overridable per service '
    'area. 500 preserves the previously hardcoded limit in '
    'backend/routes/rides/matching.py. See migration 404 header.';

COMMENT ON COLUMN public.settings.dispatch_geo_provider IS
    'Global dispatch candidate geo provider: legacy | shadow | postgis | h3. '
    'Default changed legacy -> postgis in migration 404: legacy is an '
    'unordered LIMIT over a bounding box (nearest driver not guaranteed to '
    'be in the returned pool), while postgis uses the '
    'drivers_nearby_location_geog RPC (migration 398) with ORDER BY '
    'ST_Distance over a GiST index for true nearest-N. '
    '_postgis_or_fallback in backend/services/dispatch_candidates.py '
    'auto-falls-back to legacy on any error.';
