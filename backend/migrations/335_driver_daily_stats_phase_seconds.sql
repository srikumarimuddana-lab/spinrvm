-- 335_driver_daily_stats_phase_seconds.sql
--
-- Rollback: ALTER TABLE driver_daily_stats
--             DROP COLUMN IF EXISTS idle_seconds,
--             DROP COLUMN IF EXISTS navigating_seconds,
--             DROP COLUMN IF EXISTS trip_seconds,
--             DROP COLUMN IF EXISTS day_tz;
--
-- Purpose: the Distance Travelled admin table (tracking-overhaul Phase 2)
-- shows per-phase DURATION alongside km ("Driving around 2.1 km / 38 min").
-- driver_daily_stats (migration 16) only stored km. Adds the three
-- per-phase second counters plus a day_tz discriminator:
--
--   day_tz = 'utc'    — legacy rows written by the admin rollup endpoint
--                       using UTC day boundaries (all pre-existing rows).
--   day_tz = 'regina' — rows written by the scheduled rollup loop using
--                       America/Regina day boundaries (the business day;
--                       Saskatchewan is UTC-6 year-round, no DST).
--
-- Additive only: every existing reader (drivers/earnings.py,
-- drivers/referrals.py leaderboard RPC, admin drivers stats endpoint)
-- selects named columns and is unaffected by new columns with defaults.
-- The UNIQUE (driver_id, stat_date) key is unchanged — when the Regina
-- rollup recomputes a date that a legacy UTC row occupies, it overwrites
-- it in place and stamps day_tz='regina' (a deliberate correction to the
-- business-day definition, not a duplicate).

ALTER TABLE driver_daily_stats
    ADD COLUMN IF NOT EXISTS idle_seconds       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS navigating_seconds INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS trip_seconds       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS day_tz             TEXT    NOT NULL DEFAULT 'utc';

COMMENT ON COLUMN driver_daily_stats.idle_seconds IS
  'Seconds moving while online_idle (accepted GPS segments only, gaps > 5 min excluded).';
COMMENT ON COLUMN driver_daily_stats.navigating_seconds IS
  'Seconds in navigating_to_pickup + arrived_at_pickup (the "on pickup way" bucket).';
COMMENT ON COLUMN driver_daily_stats.trip_seconds IS
  'Seconds in trip_in_progress (passenger aboard) from accepted GPS segments.';
COMMENT ON COLUMN driver_daily_stats.day_tz IS
  'Day-boundary definition for this row: utc (legacy endpoint) or regina (scheduled rollup, business day).';
