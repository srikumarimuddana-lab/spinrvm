-- Enable PostGIS for location features
create extension if not exists postgis;

-- 1. PROFILES (Public User Data)
-- Linked to Firebase Auth ID (we will store the Firebase UID in the 'id' column)
create table public.profiles (
  id text primary key, -- Firebase UID
  phone text unique,
  first_name text,
  last_name text,
  email text,
  avatar_url text,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- 2. DRIVERS (Extends Profile)
create table public.drivers (
  id text primary key references public.profiles(id),
  is_online boolean default false,
  is_verified boolean default false,
  
  -- Vehicle Info
  vehicle_make text,
  vehicle_model text,
  vehicle_color text,
  license_plate text,
  
  -- Location (PostGIS Point)
  current_location geography(POINT),
  last_location_update timestamp with time zone,
  
  -- Earnings
  total_earnings decimal(10,2) default 0.00,
  rating decimal(3,2) default 5.00
);

-- 3. RIDES
create type ride_status as enum (
  'searching', 'accepted', 'arrived', 'in_progress', 'completed', 'cancelled'
);

create table public.rides (
  id uuid default gen_random_uuid() primary key,
  rider_id text references public.profiles(id) not null,
  driver_id text references public.drivers(id), -- Nullable until accepted
  
  status ride_status default 'searching',
  
  -- Locations
  pickup_address text,
  pickup_location geography(POINT) not null,
  dropoff_address text,
  dropoff_location geography(POINT) not null,
  
  -- Fare & Payment
  estimated_fare decimal(10,2),
  final_fare decimal(10,2),
  payment_status text default 'pending',
  
  -- Security
  pickup_otp text, -- The 4 digit code
  
  created_at timestamp with time zone default timezone('utc'::text, now()) not null,
  completed_at timestamp with time zone
);

-- 4. INDEXES (For fast searching)
-- Index for "Find nearest driver"
create index drivers_location_idx on public.drivers using GIST (current_location);

-- 5. ROW LEVEL SECURITY (RLS)
alter table public.profiles enable row level security;
alter table public.drivers enable row level security;
alter table public.rides enable row level security;

-- Policies (Simplified for initial setup)

-- Profiles: Everyone can read, only owner can update
create policy "Public profiles are viewable by everyone"
  on profiles for select
  using ( true );

create policy "Users can insert their own profile"
  on profiles for insert
  with check ( true ); -- In real app, verify auth.uid() matches

create policy "Users can update own profile"
  on profiles for update
  using ( true ); -- In real app, verify auth.uid() matches

-- Drivers: Online drivers are viewable by everyone (for map)
create policy "Online drivers are public"
  on drivers for select
  using ( is_online = true );

-- Rides: Only involved parties can see
create policy "Riders can see their own rides"
  on rides for select
  using ( rider_id = auth.uid()::text or driver_id = auth.uid()::text );