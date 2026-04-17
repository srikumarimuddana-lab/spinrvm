-- Complete schema gap-filler (OPS-002)
-- Adds every table and column the Spinr backend writes to that is missing
-- from FINAL_SCHEMA.sql. All statements are idempotent (IF NOT EXISTS /
-- ADD COLUMN IF NOT EXISTS) so this is safe to run against an existing DB.

-- ============================================================
-- Missing columns on existing tables
-- ============================================================

-- users --
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='gender') THEN
        ALTER TABLE users ADD COLUMN gender TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='profile_image') THEN
        ALTER TABLE users ADD COLUMN profile_image TEXT;          -- Base64 or URL
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='profile_image_status') THEN
        ALTER TABLE users ADD COLUMN profile_image_status TEXT;   -- pending_review | approved | rejected
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='current_session_id') THEN
        ALTER TABLE users ADD COLUMN current_session_id TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='is_driver') THEN
        ALTER TABLE users ADD COLUMN is_driver BOOLEAN DEFAULT false;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='corporate_account_id') THEN
        ALTER TABLE users ADD COLUMN corporate_account_id TEXT;
    END IF;
END $$;

-- drivers --
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='lat') THEN
        ALTER TABLE drivers ADD COLUMN lat DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='lng') THEN
        ALTER TABLE drivers ADD COLUMN lng DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='city') THEN
        ALTER TABLE drivers ADD COLUMN city TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='license_number') THEN
        ALTER TABLE drivers ADD COLUMN license_number TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='license_expiry_date') THEN
        ALTER TABLE drivers ADD COLUMN license_expiry_date TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='work_eligibility_expiry_date') THEN
        ALTER TABLE drivers ADD COLUMN work_eligibility_expiry_date TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='vehicle_year') THEN
        ALTER TABLE drivers ADD COLUMN vehicle_year INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='vehicle_vin') THEN
        ALTER TABLE drivers ADD COLUMN vehicle_vin TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='vehicle_inspection_expiry_date') THEN
        ALTER TABLE drivers ADD COLUMN vehicle_inspection_expiry_date TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='insurance_expiry_date') THEN
        ALTER TABLE drivers ADD COLUMN insurance_expiry_date TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='background_check_expiry_date') THEN
        ALTER TABLE drivers ADD COLUMN background_check_expiry_date TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='documents') THEN
        ALTER TABLE drivers ADD COLUMN documents JSONB DEFAULT '{}'::jsonb;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='is_verified') THEN
        ALTER TABLE drivers ADD COLUMN is_verified BOOLEAN DEFAULT false;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='rejection_reason') THEN
        ALTER TABLE drivers ADD COLUMN rejection_reason TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='submitted_at') THEN
        ALTER TABLE drivers ADD COLUMN submitted_at TIMESTAMPTZ;
    END IF;
END $$;

-- service_areas --
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='service_areas' AND column_name='polygon') THEN
        ALTER TABLE service_areas ADD COLUMN polygon JSONB;       -- legacy lat/lng polygon array
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='service_areas' AND column_name='is_airport') THEN
        ALTER TABLE service_areas ADD COLUMN is_airport BOOLEAN DEFAULT false;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='service_areas' AND column_name='airport_fee') THEN
        ALTER TABLE service_areas ADD COLUMN airport_fee DOUBLE PRECISION DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='service_areas' AND column_name='surge_multiplier') THEN
        ALTER TABLE service_areas ADD COLUMN surge_multiplier DOUBLE PRECISION DEFAULT 1.0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='service_areas' AND column_name='free_cancel_window_seconds') THEN
        ALTER TABLE service_areas ADD COLUMN free_cancel_window_seconds INTEGER DEFAULT 120;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='service_areas' AND column_name='updated_at') THEN
        ALTER TABLE service_areas ADD COLUMN updated_at TIMESTAMPTZ DEFAULT now();
    END IF;
END $$;

-- rides --
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='pickup_lat') THEN
        ALTER TABLE rides ADD COLUMN pickup_lat DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='pickup_lng') THEN
        ALTER TABLE rides ADD COLUMN pickup_lng DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='dropoff_lat') THEN
        ALTER TABLE rides ADD COLUMN dropoff_lat DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='dropoff_lng') THEN
        ALTER TABLE rides ADD COLUMN dropoff_lng DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='stops') THEN
        ALTER TABLE rides ADD COLUMN stops JSONB DEFAULT '[]'::jsonb;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='is_scheduled') THEN
        ALTER TABLE rides ADD COLUMN is_scheduled BOOLEAN DEFAULT false;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='scheduled_time') THEN
        ALTER TABLE rides ADD COLUMN scheduled_time TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='corporate_account_id') THEN
        ALTER TABLE rides ADD COLUMN corporate_account_id TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='surge_multiplier') THEN
        ALTER TABLE rides ADD COLUMN surge_multiplier DOUBLE PRECISION DEFAULT 1.0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='distance_fare') THEN
        ALTER TABLE rides ADD COLUMN distance_fare DOUBLE PRECISION DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='time_fare') THEN
        ALTER TABLE rides ADD COLUMN time_fare DOUBLE PRECISION DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='rider_rating') THEN
        ALTER TABLE rides ADD COLUMN rider_rating INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='rider_comment') THEN
        ALTER TABLE rides ADD COLUMN rider_comment TEXT;
    END IF;
END $$;

-- saved_addresses --
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='saved_addresses' AND column_name='lat') THEN
        ALTER TABLE saved_addresses ADD COLUMN lat DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='saved_addresses' AND column_name='lng') THEN
        ALTER TABLE saved_addresses ADD COLUMN lng DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='saved_addresses' AND column_name='address') THEN
        ALTER TABLE saved_addresses ADD COLUMN address TEXT;
    END IF;
END $$;

-- ============================================================
-- New tables
-- ============================================================

-- Refresh tokens (JWT rotation — SEC-015)
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT        NOT NULL,
    token_hash      TEXT        UNIQUE NOT NULL,   -- SHA-256 of raw token
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked         BOOLEAN     NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS refresh_tokens_user_id_idx ON refresh_tokens (user_id);
CREATE INDEX IF NOT EXISTS refresh_tokens_hash_idx    ON refresh_tokens (token_hash);

-- Driver location history (real-time tracking)
CREATE TABLE IF NOT EXISTS driver_location_history (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id   TEXT        NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lng         DOUBLE PRECISION NOT NULL,
    heading     DOUBLE PRECISION,
    speed       DOUBLE PRECISION,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS driver_loc_history_driver_idx ON driver_location_history (driver_id);
CREATE INDEX IF NOT EXISTS driver_loc_history_time_idx   ON driver_location_history (recorded_at DESC);

-- Ride messages / in-app chat
CREATE TABLE IF NOT EXISTS ride_messages (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id     TEXT        NOT NULL,
    sender_id   TEXT        NOT NULL,
    sender_role TEXT        NOT NULL,   -- rider | driver
    message     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ride_messages_ride_idx ON ride_messages (ride_id);

-- Subscription plans (Spinr Pass)
CREATE TABLE IF NOT EXISTS subscription_plans (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    billing_period  TEXT        NOT NULL DEFAULT 'monthly',
    features        JSONB       DEFAULT '[]'::jsonb,
    is_active       BOOLEAN     DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Driver subscriptions
CREATE TABLE IF NOT EXISTS driver_subscriptions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id           TEXT        NOT NULL,
    plan_id             TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'active',  -- active | cancelled | expired
    current_period_start TIMESTAMPTZ,
    current_period_end   TIMESTAMPTZ,
    stripe_subscription_id TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS driver_subscriptions_driver_idx ON driver_subscriptions (driver_id);

-- Promotions / discount codes
CREATE TABLE IF NOT EXISTS promotions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code            TEXT        UNIQUE NOT NULL,
    description     TEXT,
    discount_type   TEXT        NOT NULL DEFAULT 'percent',  -- percent | fixed
    discount_value  DOUBLE PRECISION NOT NULL,
    max_uses        INTEGER,
    times_used      INTEGER     DEFAULT 0,
    valid_from      TIMESTAMPTZ,
    valid_until     TIMESTAMPTZ,
    is_active       BOOLEAN     DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Driver documents (individual document rows)
CREATE TABLE IF NOT EXISTS documents (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id       TEXT        NOT NULL,
    document_type   TEXT        NOT NULL,   -- license_front | insurance | etc.
    file_url        TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending_review',
    expires_at      TIMESTAMPTZ,
    uploaded_at     TIMESTAMPTZ DEFAULT now(),
    reviewed_at     TIMESTAMPTZ,
    reviewer_notes  TEXT
);
CREATE INDEX IF NOT EXISTS documents_driver_idx ON documents (driver_id);

-- Corporate accounts
CREATE TABLE IF NOT EXISTS corporate_accounts (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL,
    billing_email   TEXT,
    monthly_limit   DOUBLE PRECISION,
    is_active       BOOLEAN     DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Corporate ride linkage
CREATE TABLE IF NOT EXISTS corporate_rides (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id             TEXT        NOT NULL,
    corporate_account_id TEXT       NOT NULL,
    billed_at           TIMESTAMPTZ,
    amount              DOUBLE PRECISION
);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_logs (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id    TEXT,
    actor_role  TEXT,
    action      TEXT        NOT NULL,
    resource    TEXT,
    resource_id TEXT,
    details     JSONB,
    ip_address  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_logs_actor_idx    ON audit_logs (actor_id);
CREATE INDEX IF NOT EXISTS audit_logs_resource_idx ON audit_logs (resource, resource_id);
CREATE INDEX IF NOT EXISTS audit_logs_time_idx     ON audit_logs (created_at DESC);

-- Cloud / push messages
CREATE TABLE IF NOT EXISTS cloud_messages (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT        NOT NULL,
    body            TEXT        NOT NULL,
    data            JSONB       DEFAULT '{}'::jsonb,
    audience_type   TEXT        NOT NULL DEFAULT 'all',  -- all | riders | drivers | segment
    status          TEXT        NOT NULL DEFAULT 'draft', -- draft | scheduled | sent | failed
    scheduled_at    TIMESTAMPTZ,
    sent_at         TIMESTAMPTZ,
    recipient_count INTEGER     DEFAULT 0,
    created_by      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Disputes
CREATE TABLE IF NOT EXISTS disputes (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id     TEXT,
    reporter_id TEXT        NOT NULL,
    type        TEXT        NOT NULL,   -- fare | safety | behaviour | other
    description TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'open',  -- open | investigating | resolved | closed
    resolution  TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

-- Staff / admin users
CREATE TABLE IF NOT EXISTS staff (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT        UNIQUE NOT NULL,
    name        TEXT,
    role        TEXT        NOT NULL DEFAULT 'support',  -- admin | support | finance
    is_active   BOOLEAN     DEFAULT true,
    last_login  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Emergency contacts
CREATE TABLE IF NOT EXISTS emergency_contacts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    phone       TEXT        NOT NULL,
    relationship TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS emergency_contacts_user_idx ON emergency_contacts (user_id);
