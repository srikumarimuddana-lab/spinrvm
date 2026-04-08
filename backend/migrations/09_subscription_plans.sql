-- Migration: Create subscription_plans and driver_subscriptions tables for Spinr Pass

CREATE TABLE IF NOT EXISTS subscription_plans (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    price           FLOAT NOT NULL DEFAULT 0,
    duration_days   INTEGER NOT NULL DEFAULT 30,
    rides_per_day   INTEGER NOT NULL DEFAULT -1,  -- -1 = unlimited
    description     TEXT DEFAULT '',
    features        JSONB DEFAULT '[]'::JSONB,
    vehicle_types   JSONB DEFAULT NULL,           -- null = all types
    service_areas   JSONB DEFAULT NULL,           -- null = all areas
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    subscriber_count INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS driver_subscriptions (
    id              TEXT PRIMARY KEY,
    driver_id       TEXT NOT NULL,
    plan_id         TEXT REFERENCES subscription_plans(id) ON DELETE SET NULL,
    plan_name       TEXT,
    price           FLOAT NOT NULL DEFAULT 0,
    rides_per_day   INTEGER NOT NULL DEFAULT -1,
    status          TEXT NOT NULL DEFAULT 'active',   -- active, expired, cancelled
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    cancelled_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_driver_subscriptions_driver ON driver_subscriptions(driver_id);
CREATE INDEX IF NOT EXISTS idx_driver_subscriptions_status ON driver_subscriptions(status);
