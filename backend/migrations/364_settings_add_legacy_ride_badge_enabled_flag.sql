-- 364: rollout flag for the legacy-imported-ride "Imported" badge + no-GPS
-- disclaimer on rider/driver ride-detail screens
-- (docs/legacy-ride-history-presentation-plan.md, Item 2).
--
-- Context: routes/rides/queries.py's GET /{ride_id} already returns
-- legacy_import_metadata on every ride response, unflagged, and always has
-- -- this migration does not change that. What this flag gates is a new
-- show_legacy_badge computed field on that same response: true only when
-- legacy_ride_badge_enabled is on AND the ride's legacy_import_metadata is
-- non-empty. Rider-app/driver-app read this field to decide whether to show
-- an "Imported" badge and a "no GPS was recorded for this ride" disclaimer
-- next to the map -- mirroring what admin-dashboard's ride-detail-modal.tsx
-- already shows unconditionally. Neither app reads this field yet as of
-- this migration; wiring them up is a separate follow-up (see the plan
-- doc's Items 3-4), so flipping this on today is a no-op for both apps
-- until that follow-up ships.
--
-- Fixed-flat-column `settings` table (see migration 313/353/356's own
-- headers) -- additive defaulted column, same pattern as
-- legacy_consent_notice_enabled (356).
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS legacy_ride_badge_enabled;
--
-- Forward-compatible: additive defaulted column; older backends ignore it.

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS legacy_ride_badge_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.settings.legacy_ride_badge_enabled IS
    'Dark-launch gate for the legacy-imported-ride "Imported" badge + '
    'no-GPS disclaimer on rider/driver ride-detail screens '
    '(routes/rides/queries.py GET /{ride_id}, '
    'docs/legacy-ride-history-presentation-plan.md). Off = show_legacy_badge '
    'is always false on the ride-detail response, regardless of whether '
    'legacy_import_metadata is populated -- the metadata itself is always '
    'returned either way, this flag only gates the badge/disclaimer UX. '
    'On = true for any ride with non-empty legacy_import_metadata. Still a '
    'no-op until rider-app/driver-app are wired to read the field '
    '(separate follow-up).';
