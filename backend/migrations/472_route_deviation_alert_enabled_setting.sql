-- 472_route_deviation_alert_enabled_setting.sql
--
-- Restores a column that already exists live in production but has no
-- source-controlled migration creating it. Production's `settings` table
-- carries `route_deviation_alert_enabled boolean NOT NULL DEFAULT false`,
-- and production's schema_migrations tracks a file named
-- 415_route_deviation_alert_enabled_setting.sql -- but no file under
-- backend/migrations/ actually defines it (415 in this repo is
-- 415_directions_proxy_enabled_flag.sql, an unrelated change). A database
-- rebuilt from this repo alone would be missing the column, and
-- backend/utils/route_deviation_alerter.py's `_deviation_alert_enabled()`
-- (route_deviation_alerter.py:~133) would read `settings.get(...)` as None
-- and silently keep the route-deviation safety alert off. See
-- docs/audit/clean-sheet/10-live-checks.md §6.1.
--
-- Deliberately renumbered rather than reusing 415: this repo's 415 slot is
-- already taken by a different, unrelated migration, and CI's CHECK B
-- hard-fails a new migration whose numeric prefix collides with or
-- precedes one that already exists. `ADD COLUMN IF NOT EXISTS` makes this
-- a no-op against production, where the column is already present under
-- the original (untracked) name, and a normal forward CREATE against any
-- environment rebuilt from a clean schema.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS route_deviation_alert_enabled;
-- (safe: the loop reads this column via .get() with a None-safe default of
-- disabled, so dropping it just returns the alert to its fail-closed state,
-- same as before this file existed)

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS route_deviation_alert_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.route_deviation_alert_enabled IS
    'Kill-switch for the route-deviation safety alert loop (backend/utils/route_deviation_alerter.py). Fails closed (disabled) if unreadable. Default false.';
