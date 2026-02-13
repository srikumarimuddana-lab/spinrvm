-- ============================================================
-- Spinr – Complete Supabase Schema
-- Run this in the Supabase SQL Editor (Settings → SQL Editor)
-- ============================================================

-- Enable UUID extension (usually already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. USERS
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    phone           TEXT NOT NULL UNIQUE,
    first_name      TEXT,
    last_name       TEXT,
    email           TEXT,
    gender          TEXT,                                -- Male | Female | Other
    role            TEXT NOT NULL DEFAULT 'rider',       -- rider | driver | admin
    profile_complete BOOLEAN NOT NULL DEFAULT FALSE,
    is_driver       BOOLEAN NOT NULL DEFAULT FALSE,
    fcm_token       TEXT,                                 -- push notification token
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_phone ON users (phone);

-- ============================================================
-- 2. OTP_RECORDS
-- ============================================================
CREATE TABLE IF NOT EXISTS otp_records (
    id              TEXT PRIMARY KEY,
    phone           TEXT NOT NULL,
    code            TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    verified        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_otp_phone ON otp_records (phone);

-- ============================================================
-- 3. DRIVERS
-- ============================================================
CREATE TABLE IF NOT EXISTS drivers (
    id              TEXT PRIMARY KEY,
    user_id         TEXT REFERENCES users(id),
    name            TEXT NOT NULL,
    phone           TEXT NOT NULL,
    photo_url       TEXT DEFAULT '',
    vehicle_type_id TEXT,
    vehicle_make    TEXT NOT NULL DEFAULT '',
    vehicle_model   TEXT NOT NULL DEFAULT '',
    vehicle_color   TEXT NOT NULL DEFAULT '',
    license_plate   TEXT NOT NULL DEFAULT '',

    -- Verification & Compliance
    license_number                TEXT,
    license_expiry_date           TIMESTAMPTZ,
    work_eligibility_expiry_date  TIMESTAMPTZ,
    vehicle_year                  INTEGER,
    vehicle_vin                   TEXT,
    vehicle_inspection_expiry_date TIMESTAMPTZ,
    insurance_expiry_date         TIMESTAMPTZ,
    background_check_expiry_date  TIMESTAMPTZ,
    documents                     JSONB DEFAULT '{}'::JSONB,   -- {"license_front": "url", ...}
    is_verified                   BOOLEAN NOT NULL DEFAULT FALSE,
    rejection_reason              TEXT,
    submitted_at                  TIMESTAMPTZ,

    -- Operations
    rating          FLOAT NOT NULL DEFAULT 5.0,
    total_rides     INTEGER NOT NULL DEFAULT 0,
    lat             FLOAT NOT NULL DEFAULT 0,
    lng             FLOAT NOT NULL DEFAULT 0,
    is_online       BOOLEAN NOT NULL DEFAULT TRUE,
    is_available    BOOLEAN NOT NULL DEFAULT TRUE,
    service_area_id TEXT,                                      -- assigned area (optional)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_drivers_user_id    ON drivers (user_id);
CREATE INDEX IF NOT EXISTS idx_drivers_available   ON drivers (is_available, is_online);

-- ============================================================
-- 4. SERVICE_AREAS
-- ============================================================
CREATE TABLE IF NOT EXISTS service_areas (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    city            TEXT NOT NULL,
    polygon         JSONB NOT NULL DEFAULT '[]'::JSONB,  -- [{lat, lng}, ...]
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    is_airport      BOOLEAN NOT NULL DEFAULT FALSE,
    airport_fee     FLOAT NOT NULL DEFAULT 0,

    -- Surge pricing
    surge_active    BOOLEAN NOT NULL DEFAULT FALSE,
    surge_multiplier FLOAT NOT NULL DEFAULT 1.0,

    -- Tax config stored on the area
    gst_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    gst_rate        FLOAT NOT NULL DEFAULT 5.0,
    pst_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    pst_rate        FLOAT NOT NULL DEFAULT 0.0,
    hst_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    hst_rate        FLOAT NOT NULL DEFAULT 0.0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 5. VEHICLE_TYPES
-- ============================================================
CREATE TABLE IF NOT EXISTS vehicle_types (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    icon            TEXT NOT NULL DEFAULT '',
    capacity        INTEGER NOT NULL DEFAULT 4,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 6. FARE_CONFIGS
-- ============================================================
CREATE TABLE IF NOT EXISTS fare_configs (
    id              TEXT PRIMARY KEY,
    service_area_id TEXT REFERENCES service_areas(id),
    vehicle_type_id TEXT REFERENCES vehicle_types(id),
    base_fare       FLOAT NOT NULL DEFAULT 3.50,
    per_km_rate     FLOAT NOT NULL DEFAULT 1.50,
    per_minute_rate FLOAT NOT NULL DEFAULT 0.25,
    minimum_fare    FLOAT NOT NULL DEFAULT 8.0,
    booking_fee     FLOAT NOT NULL DEFAULT 2.0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fare_vehicle ON fare_configs (vehicle_type_id);
CREATE INDEX IF NOT EXISTS idx_fare_area    ON fare_configs (service_area_id);

-- ============================================================
-- 7. RIDES
-- ============================================================
CREATE TABLE IF NOT EXISTS rides (
    id                  TEXT PRIMARY KEY,
    rider_id            TEXT REFERENCES users(id),
    driver_id           TEXT,
    vehicle_type_id     TEXT,
    pickup_address      TEXT NOT NULL,
    pickup_lat          FLOAT NOT NULL,
    pickup_lng          FLOAT NOT NULL,
    dropoff_address     TEXT NOT NULL,
    dropoff_lat         FLOAT NOT NULL,
    dropoff_lng         FLOAT NOT NULL,
    distance_km         FLOAT NOT NULL DEFAULT 0,
    duration_minutes    INTEGER NOT NULL DEFAULT 0,

    -- Fares
    base_fare           FLOAT NOT NULL DEFAULT 0,
    distance_fare       FLOAT NOT NULL DEFAULT 0,
    time_fare           FLOAT NOT NULL DEFAULT 0,
    booking_fee         FLOAT NOT NULL DEFAULT 2.0,
    airport_fee         FLOAT NOT NULL DEFAULT 0,
    airport_zone_name   TEXT,
    total_fare          FLOAT NOT NULL DEFAULT 0,
    tip_amount          FLOAT NOT NULL DEFAULT 0,

    -- Payment
    payment_method      TEXT NOT NULL DEFAULT 'card',
    payment_intent_id   TEXT,
    payment_status      TEXT NOT NULL DEFAULT 'pending',

    -- Status
    status              TEXT NOT NULL DEFAULT 'searching',
    pickup_otp          TEXT DEFAULT '',

    -- Scheduling
    is_scheduled        BOOLEAN NOT NULL DEFAULT FALSE,
    scheduled_time      TIMESTAMPTZ,

    -- Multi-stop
    stops               JSONB DEFAULT '[]'::JSONB,

    -- Trip sharing
    shared_trip_token   TEXT,
    shared_with         JSONB DEFAULT '[]'::JSONB,

    -- Timeline tracking
    ride_requested_at   TIMESTAMPTZ DEFAULT NOW(),
    driver_notified_at  TIMESTAMPTZ,
    driver_accepted_at  TIMESTAMPTZ,
    driver_arrived_at   TIMESTAMPTZ,
    ride_started_at     TIMESTAMPTZ,
    ride_completed_at   TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ,

    -- Earnings split
    driver_earnings     FLOAT NOT NULL DEFAULT 0,
    admin_earnings      FLOAT NOT NULL DEFAULT 0,
    cancellation_fee_driver FLOAT NOT NULL DEFAULT 0,
    cancellation_fee_admin  FLOAT NOT NULL DEFAULT 0,

    -- Rating
    rider_rating        INTEGER,
    rider_comment       TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rides_rider    ON rides (rider_id);
CREATE INDEX IF NOT EXISTS idx_rides_driver   ON rides (driver_id);
CREATE INDEX IF NOT EXISTS idx_rides_status   ON rides (status);
CREATE INDEX IF NOT EXISTS idx_rides_scheduled ON rides (is_scheduled, status);

-- ============================================================
-- 8. SAVED_ADDRESSES
-- ============================================================
CREATE TABLE IF NOT EXISTS saved_addresses (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    name            TEXT NOT NULL,
    address         TEXT NOT NULL,
    lat             FLOAT NOT NULL,
    lng             FLOAT NOT NULL,
    icon            TEXT DEFAULT 'location',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_saved_addr_user ON saved_addresses (user_id);

-- ============================================================
-- 9. SETTINGS  (single-row app config)
-- ============================================================
CREATE TABLE IF NOT EXISTS settings (
    id                      TEXT PRIMARY KEY DEFAULT 'app_settings',
    google_maps_api_key     TEXT DEFAULT '',
    stripe_publishable_key  TEXT DEFAULT '',
    stripe_secret_key       TEXT DEFAULT '',
    stripe_webhook_secret   TEXT DEFAULT '',
    twilio_account_sid      TEXT DEFAULT '',
    twilio_auth_token       TEXT DEFAULT '',
    twilio_from_number      TEXT DEFAULT '',
    driver_matching_algorithm TEXT DEFAULT 'nearest',
    min_driver_rating       FLOAT DEFAULT 4.0,
    search_radius_km        FLOAT DEFAULT 10.0,
    cancellation_fee_admin  FLOAT DEFAULT 0.50,
    cancellation_fee_driver FLOAT DEFAULT 2.50,
    platform_fee_percent    FLOAT DEFAULT 0.0,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default settings row
INSERT INTO settings (id) VALUES ('app_settings')
ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 10. SUPPORT_TICKETS
-- ============================================================
CREATE TABLE IF NOT EXISTS support_tickets (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    subject         TEXT NOT NULL,
    message         TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'general',
    status          TEXT NOT NULL DEFAULT 'open',        -- open | in_progress | closed
    replies         JSONB DEFAULT '[]'::JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tickets_user   ON support_tickets (user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON support_tickets (status);

-- ============================================================
-- 11. FAQS
-- ============================================================
CREATE TABLE IF NOT EXISTS faqs (
    id              TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'general',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_faqs_active ON faqs (is_active, sort_order);

-- ============================================================
-- 12. AREA_FEES
-- ============================================================
CREATE TABLE IF NOT EXISTS area_fees (
    id              TEXT PRIMARY KEY,
    service_area_id TEXT NOT NULL REFERENCES service_areas(id),
    fee_name        TEXT NOT NULL,
    fee_type        TEXT NOT NULL DEFAULT 'custom',      -- airport | night | toll | event | holiday | custom
    calc_mode       TEXT NOT NULL DEFAULT 'flat',         -- flat | per_km | percentage
    amount          FLOAT NOT NULL DEFAULT 0,
    description     TEXT,
    conditions      JSONB DEFAULT '{}'::JSONB,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_area_fees_area ON area_fees (service_area_id);

-- ============================================================
-- RPC: find_nearby_drivers  (used by ride matching)
-- ============================================================
DROP FUNCTION IF EXISTS find_nearby_drivers(FLOAT, FLOAT, FLOAT);

CREATE OR REPLACE FUNCTION find_nearby_drivers(
    lat FLOAT,
    lng FLOAT,
    radius_meters FLOAT DEFAULT 10000
)
RETURNS SETOF drivers
LANGUAGE sql STABLE
AS $$
    SELECT *
    FROM drivers
    WHERE is_online = TRUE
      AND is_available = TRUE
      AND (
            6371000 * acos(
                cos(radians(lat)) * cos(radians(drivers.lat)) *
                cos(radians(drivers.lng) - radians(lng)) +
                sin(radians(lat)) * sin(radians(drivers.lat))
            )
          ) <= radius_meters
    ORDER BY (
            6371000 * acos(
                cos(radians(lat)) * cos(radians(drivers.lat)) *
                cos(radians(drivers.lng) - radians(lng)) +
                sin(radians(lat)) * sin(radians(drivers.lat))
            )
          ) ASC;
$$;

-- ============================================================
-- RPC: update_driver_location
-- ============================================================
DROP FUNCTION IF EXISTS update_driver_location(TEXT, FLOAT, FLOAT);

CREATE OR REPLACE FUNCTION update_driver_location(
    p_driver_id TEXT,
    lat FLOAT,
    lng FLOAT
)
RETURNS VOID
LANGUAGE sql
AS $$
    UPDATE drivers SET lat = update_driver_location.lat, lng = update_driver_location.lng
    WHERE id = p_driver_id;
$$;

-- ============================================================
-- Row Level Security (RLS)
-- Enable RLS on all tables.  The backend uses the service_role
-- key which bypasses RLS, but these policies protect the
-- anon/authenticated keys if ever used directly.
-- ============================================================

ALTER TABLE users            ENABLE ROW LEVEL SECURITY;
ALTER TABLE otp_records      ENABLE ROW LEVEL SECURITY;
ALTER TABLE drivers          ENABLE ROW LEVEL SECURITY;
ALTER TABLE rides            ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_addresses  ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings         ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_areas    ENABLE ROW LEVEL SECURITY;
ALTER TABLE vehicle_types    ENABLE ROW LEVEL SECURITY;
ALTER TABLE fare_configs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE support_tickets  ENABLE ROW LEVEL SECURITY;
ALTER TABLE faqs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE area_fees        ENABLE ROW LEVEL SECURITY;

-- Allow service_role full access (backend server)
-- Note: service_role key bypasses RLS by default in Supabase,
-- so these are mainly for reference / if you switch to anon key.

-- Public read for vehicle_types and faqs
CREATE POLICY "Public read vehicle_types"  ON vehicle_types  FOR SELECT USING (true);
CREATE POLICY "Public read faqs"           ON faqs           FOR SELECT USING (is_active = true);
CREATE POLICY "Public read service_areas"  ON service_areas  FOR SELECT USING (is_active = true);

-- ============================================================
-- 
-- Dynamic Document Requirements
--
--
-- Dynamic Document Requirements
--
CREATE TABLE IF NOT EXISTS public.document_requirements (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    description TEXT,
    is_mandatory BOOLEAN DEFAULT true,
    requires_back_side BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now())
);

-- Ensure driver_documents table exists
CREATE TABLE IF NOT EXISTS public.driver_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    driver_id TEXT REFERENCES public.drivers(id) ON DELETE CASCADE,
    document_type TEXT, -- Legacy or display name
    document_url TEXT NOT NULL,
    status TEXT DEFAULT 'pending', -- pending, approved, rejected
    rejection_reason TEXT,
    uploaded_at TIMESTAMPTZ DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ DEFAULT timezone('utc', now())
);

ALTER TABLE public.driver_documents 
ADD COLUMN IF NOT EXISTS requirement_id UUID REFERENCES public.document_requirements(id),
ADD COLUMN IF NOT EXISTS side TEXT CHECK (side IN ('front', 'back'));

ALTER TABLE public.document_requirements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.driver_documents ENABLE ROW LEVEL SECURITY;

-- Policies for document_requirements
CREATE POLICY "Public read access for requirements"
ON public.document_requirements FOR SELECT
TO authenticated, anon
USING (true);

CREATE POLICY "Admin full access for requirements"
ON public.document_requirements FOR ALL
TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM public.users 
    WHERE public.users.id = auth.uid()::text 
    AND public.users.role = 'admin'
  )
);

-- Policies for driver_documents
CREATE POLICY "Drivers can view own documents"
ON public.driver_documents FOR SELECT
TO authenticated
USING (
  driver_id IN (
    SELECT id FROM public.drivers WHERE user_id = auth.uid()::text
  )
);

CREATE POLICY "Drivers can insert own documents"
ON public.driver_documents FOR INSERT
TO authenticated
WITH CHECK (
  driver_id IN (
    SELECT id FROM public.drivers WHERE user_id = auth.uid()::text
  )
);

CREATE POLICY "Admin full access for documents"
ON public.driver_documents FOR ALL
TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM public.users 
    WHERE public.users.id = auth.uid()::text 
    AND public.users.role = 'admin'
  )
);


-- Seed defaults
INSERT INTO public.document_requirements (name, description, is_mandatory, requires_back_side)
SELECT 'Driving License', 'Valid driving license', true, true
WHERE NOT EXISTS (SELECT 1 FROM public.document_requirements WHERE name = 'Driving License');

INSERT INTO public.document_requirements (name, description, is_mandatory, requires_back_side)
SELECT 'Vehicle Insurance', 'Valid vehicle insurance policy', true, false
WHERE NOT EXISTS (SELECT 1 FROM public.document_requirements WHERE name = 'Vehicle Insurance');

INSERT INTO public.document_requirements (name, description, is_mandatory, requires_back_side)
SELECT 'Vehicle Inspection', 'Vehicle inspection report', true, false
WHERE NOT EXISTS (SELECT 1 FROM public.document_requirements WHERE name = 'Vehicle Inspection');

