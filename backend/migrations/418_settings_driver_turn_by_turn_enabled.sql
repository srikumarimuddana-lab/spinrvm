-- 418: admin-settable column for the driver_turn_by_turn_enabled dark-launch
-- flag (docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md,
-- schemas.py's AppSettings field added in Phase 1 PR A / #5289).
--
-- Renumbered twice before merge: drafted as 416, renumbered to 417 when PR
-- #5307 merged its own unrelated 416 first, then renumbered again to 418
-- when a second unrelated migration (417_webhook_preauth_failure_ack_flag.sql)
-- landed on main while this PR was still open. No SQL content changed either
-- time, only the number and its cross-references (see this PR's Change
-- Impact Log and ACTION_ITEMS.md C110 for the fuller history).
--
-- Context: the flag has existed in AppSettings since PR A, and GET
-- /api/admin/settings already returns it correctly via the schema-default
-- merge (get_app_settings()). But nothing could ever turn it ON: PUT
-- /api/admin/settings builds its update payload from SettingsUpdateRequest,
-- a separate hand-maintained Pydantic model that never declared this field,
-- so any attempt to set it was silently dropped (model_config extra="ignore").
--
-- Fixed-flat-column `settings` table (migration 313's own header: "there is
-- no JSON catch-all") -- a new AppSettings/admin-PUT field is not safe to
-- add without a matching migration, or PUT /api/admin/settings 500s on the
-- unknown column (PGRST204) -- see test_settings_column_parity.py and
-- migration 313/353/415's own header for the prior incidents this exact
-- failure mode caused. This migration adds the column so the accompanying
-- SettingsUpdateRequest field (added in the same PR) has somewhere to land.
--
-- Default false, matching AppSettings' own schema default and the ship-dark
-- rule already in effect since PR A -- this migration does not change any
-- live behavior on its own.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS driver_turn_by_turn_enabled;
--
-- Forward-compatible: additive defaulted column; older backends ignore it.

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS driver_turn_by_turn_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.settings.driver_turn_by_turn_enabled IS
    'Dark-launch gate for driver-app in-app turn-by-turn navigation '
    '(docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md). '
    'Off (default) = GET /rides/{id}/navigation-steps returns an empty step '
    'list and the driver-app banner/camera-zoom/off-route-refetch features '
    '(Phase 1 PRs B/C/D, #5292/#5294/#5295) stay fully inert. On = turn-by-turn '
    'steps are fetched, cached, and shown. Ship dark, verify in staging/canary, '
    'then flip on.';
