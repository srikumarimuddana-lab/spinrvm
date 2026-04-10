-- Migration: Driver daily activity aggregates
-- Rolled up nightly from driver_location_history + rides so that historical
-- driver performance data (especially days with no rides) is preserved
-- compactly without needing to keep raw GPS points.

CREATE TABLE IF NOT EXISTS driver_daily_stats (
    id                    TEXT PRIMARY KEY,
    driver_id             TEXT NOT NULL,
    stat_date             DATE NOT NULL,
    service_area_id       TEXT,

    -- Online activity
    online_minutes        INTEGER NOT NULL DEFAULT 0,
    idle_km               FLOAT NOT NULL DEFAULT 0,   -- distance roamed while online_idle
    first_online_at       TIMESTAMPTZ,
    last_online_at        TIMESTAMPTZ,

    -- Ride activity
    rides_completed       INTEGER NOT NULL DEFAULT 0,
    rides_cancelled       INTEGER NOT NULL DEFAULT 0,
    rides_declined        INTEGER NOT NULL DEFAULT 0,
    navigating_km         FLOAT NOT NULL DEFAULT 0,   -- driver→pickup total
    trip_km               FLOAT NOT NULL DEFAULT 0,   -- paid trip total
    total_km              FLOAT NOT NULL DEFAULT 0,   -- idle + navigating + trip

    -- Earnings
    total_earnings        FLOAT NOT NULL DEFAULT 0,
    total_tips            FLOAT NOT NULL DEFAULT 0,

    -- Metadata
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (driver_id, stat_date)
);

CREATE INDEX IF NOT EXISTS idx_ddstats_driver ON driver_daily_stats(driver_id, stat_date DESC);
CREATE INDEX IF NOT EXISTS idx_ddstats_date ON driver_daily_stats(stat_date DESC);
CREATE INDEX IF NOT EXISTS idx_ddstats_area ON driver_daily_stats(service_area_id, stat_date DESC);

COMMENT ON TABLE driver_daily_stats IS
  'Nightly rollup of driver activity. Tracks roaming (idle km), rides completed, and earnings per day per driver. Enables historical performance analysis without keeping raw GPS data.';

COMMENT ON COLUMN driver_daily_stats.idle_km IS
  'Total km driven while online_idle (no active ride). Captures roaming behavior and waste.';

COMMENT ON COLUMN driver_daily_stats.online_minutes IS
  'Total minutes driver was online that day (computed from driver_location_history timestamps).';
