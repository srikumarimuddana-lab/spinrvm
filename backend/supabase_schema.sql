-- Supabase/Postgres schema for Spinr (minimal)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users
CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY,
  original_mongo_id text,
  phone text,
  first_name text,
  last_name text,
  email text,
  city text,
  role text,
  profile_complete boolean DEFAULT false,
  created_at timestamptz,
  updated_at timestamptz
);

-- Drivers
CREATE TABLE IF NOT EXISTS drivers (
  id uuid PRIMARY KEY,
  original_mongo_id text,
  name text,
  phone text,
  photo_url text,
  vehicle_type_id uuid,
  vehicle_make text,
  vehicle_model text,
  vehicle_color text,
  license_plate text,
  rating numeric,
  total_rides integer,
  lat double precision,
  lng double precision,
  is_online boolean,
  is_available boolean,
  created_at timestamptz
);

-- Vehicle types
CREATE TABLE IF NOT EXISTS vehicle_types (
  id uuid PRIMARY KEY,
  original_mongo_id text,
  name text,
  description text,
  icon text,
  capacity integer,
  is_active boolean,
  created_at timestamptz
);

-- Service areas (store polygon as json)
CREATE TABLE IF NOT EXISTS service_areas (
  id uuid PRIMARY KEY,
  original_mongo_id text,
  name text,
  city text,
  polygon jsonb,
  is_active boolean,
  created_at timestamptz
);

-- Fare configs
CREATE TABLE IF NOT EXISTS fare_configs (
  id uuid PRIMARY KEY,
  original_mongo_id text,
  service_area_id uuid,
  vehicle_type_id uuid,
  base_fare numeric,
  per_km_rate numeric,
  per_minute_rate numeric,
  minimum_fare numeric,
  booking_fee numeric,
  is_active boolean,
  created_at timestamptz
);

-- Rides (simplified)
CREATE TABLE IF NOT EXISTS rides (
  id uuid PRIMARY KEY,
  original_mongo_id text,
  rider_id uuid,
  driver_id uuid,
  vehicle_type_id uuid,
  pickup_address text,
  pickup_lat double precision,
  pickup_lng double precision,
  dropoff_address text,
  dropoff_lat double precision,
  dropoff_lng double precision,
  distance_km numeric,
  duration_minutes integer,
  base_fare numeric,
  distance_fare numeric,
  time_fare numeric,
  booking_fee numeric,
  total_fare numeric,
  tip_amount numeric,
  payment_method text,
  payment_intent_id text,
  payment_status text,
  status text,
  pickup_otp text,
  ride_requested_at timestamptz,
  driver_notified_at timestamptz,
  driver_accepted_at timestamptz,
  driver_arrived_at timestamptz,
  ride_started_at timestamptz,
  ride_completed_at timestamptz,
  cancelled_at timestamptz,
  driver_earnings numeric,
  admin_earnings numeric,
  cancellation_fee_driver numeric,
  cancellation_fee_admin numeric,
  rider_rating integer,
  rider_comment text,
  created_at timestamptz,
  updated_at timestamptz
);

-- Saved addresses
CREATE TABLE IF NOT EXISTS saved_addresses (
  id uuid PRIMARY KEY,
  original_mongo_id text,
  user_id uuid,
  name text,
  address text,
  lat double precision,
  lng double precision,
  icon text,
  created_at timestamptz
);

-- OTP records (for migration only, consider reworking auth to Firebase)
CREATE TABLE IF NOT EXISTS otp_records (
  id uuid PRIMARY KEY,
  original_mongo_id text,
  phone text,
  code text,
  expires_at timestamptz,
  verified boolean,
  created_at timestamptz
);

-- Settings
CREATE TABLE IF NOT EXISTS settings (
  id text PRIMARY KEY,
  data jsonb,
  updated_at timestamptz
);
