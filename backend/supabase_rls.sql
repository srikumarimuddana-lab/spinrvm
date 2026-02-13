-- Row-Level Security policies for Spinr
-- Note: auth.uid() returns a UUID; cast to text when comparing against text columns.
-- The backend uses the Supabase SERVICE ROLE key, which bypasses all RLS by default.
-- These policies protect against direct client-side Supabase access.

-- ============================================================
-- Enable RLS on all tables
-- ============================================================
ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.drivers ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.rides ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.otp_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.support_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.faqs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.vehicle_types ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.fare_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.service_areas ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- USERS: own row only; inserts via server (service role)
-- ============================================================
DROP POLICY IF EXISTS users_select_self ON public.users;
CREATE POLICY users_select_self ON public.users
  FOR SELECT USING (auth.uid()::text = id);

DROP POLICY IF EXISTS users_update_self ON public.users;
CREATE POLICY users_update_self ON public.users
  FOR UPDATE USING (auth.uid()::text = id)
  WITH CHECK (auth.uid()::text = id);

DROP POLICY IF EXISTS users_delete_self ON public.users;
CREATE POLICY users_delete_self ON public.users
  FOR DELETE USING (auth.uid()::text = id);

-- ============================================================
-- DRIVERS: public SELECT (riders see nearby); UPDATE own row via user_id
-- ============================================================
DROP POLICY IF EXISTS drivers_select_public ON public.drivers;
CREATE POLICY drivers_select_public ON public.drivers
  FOR SELECT USING (true);

DROP POLICY IF EXISTS drivers_update_self ON public.drivers;
CREATE POLICY drivers_update_self ON public.drivers
  FOR UPDATE USING (auth.uid()::text = user_id)
  WITH CHECK (auth.uid()::text = user_id);

-- ============================================================
-- RIDES: rider and assigned driver can view/update their rides
-- ============================================================
DROP POLICY IF EXISTS rides_select_parties ON public.rides;
CREATE POLICY rides_select_parties ON public.rides
  FOR SELECT USING (
    auth.uid()::text = rider_id OR auth.uid()::text = driver_id
  );

DROP POLICY IF EXISTS rides_insert_rider ON public.rides;
CREATE POLICY rides_insert_rider ON public.rides
  FOR INSERT WITH CHECK (auth.uid()::text = rider_id);

DROP POLICY IF EXISTS rides_update_parties ON public.rides;
CREATE POLICY rides_update_parties ON public.rides
  FOR UPDATE USING (
    auth.uid()::text = rider_id OR auth.uid()::text = driver_id
  )
  WITH CHECK (
    auth.uid()::text = rider_id OR auth.uid()::text = driver_id
  );

-- ============================================================
-- OTP_RECORDS: no client access (server-only via service role)
-- ============================================================
DROP POLICY IF EXISTS otp_deny_all ON public.otp_records;
CREATE POLICY otp_deny_all ON public.otp_records
  FOR ALL USING (false);

-- ============================================================
-- SETTINGS: no client access (admin reads via server API)
-- ============================================================
DROP POLICY IF EXISTS settings_deny_all ON public.settings;
CREATE POLICY settings_deny_all ON public.settings
  FOR ALL USING (false);

-- ============================================================
-- SUPPORT_TICKETS: users see own tickets; server handles admin access
-- ============================================================
DROP POLICY IF EXISTS tickets_select_own ON public.support_tickets;
CREATE POLICY tickets_select_own ON public.support_tickets
  FOR SELECT USING (auth.uid()::text = user_id);

DROP POLICY IF EXISTS tickets_insert_own ON public.support_tickets;
CREATE POLICY tickets_insert_own ON public.support_tickets
  FOR INSERT WITH CHECK (auth.uid()::text = user_id);

DROP POLICY IF EXISTS tickets_update_own ON public.support_tickets;
CREATE POLICY tickets_update_own ON public.support_tickets
  FOR UPDATE USING (auth.uid()::text = user_id);

-- ============================================================
-- FAQS: public read-only; writes via server (admin service role)
-- ============================================================
DROP POLICY IF EXISTS faqs_select_public ON public.faqs;
CREATE POLICY faqs_select_public ON public.faqs
  FOR SELECT USING (true);

-- ============================================================
-- VEHICLE_TYPES / FARE_CONFIGS / SERVICE_AREAS: public read-only
-- Writes handled by admin API through server service role
-- ============================================================
DROP POLICY IF EXISTS vehicle_types_select_public ON public.vehicle_types;
CREATE POLICY vehicle_types_select_public ON public.vehicle_types
  FOR SELECT USING (true);

DROP POLICY IF EXISTS fare_configs_select_public ON public.fare_configs;
CREATE POLICY fare_configs_select_public ON public.fare_configs
  FOR SELECT USING (true);

DROP POLICY IF EXISTS service_areas_select_public ON public.service_areas;
CREATE POLICY service_areas_select_public ON public.service_areas
  FOR SELECT USING (true);

-- ============================================================
-- Notes:
-- • The backend uses the Supabase SERVICE ROLE key which bypasses all RLS.
-- • These policies protect against direct client-side Supabase usage (anon key).
-- • Run this script in the Supabase SQL editor to apply.
-- ============================================================
