-- Row-Level Security policies for Spinr
-- Note: auth.uid() relies on JWT sub claim matching the `id` stored in the table.

-- Enable RLS
ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.drivers ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.rides ENABLE ROW LEVEL SECURITY;

-- USERS: allow users to SELECT/UPDATE/DELETE their own row; INSERT via server only (no public insert policy)
DROP POLICY IF EXISTS users_select_self ON public.users;
CREATE POLICY users_select_self ON public.users FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS users_update_self ON public.users;
CREATE POLICY users_update_self ON public.users FOR UPDATE USING (auth.uid() = id) WITH CHECK (auth.uid() = id);

DROP POLICY IF EXISTS users_delete_self ON public.users;
CREATE POLICY users_delete_self ON public.users FOR DELETE USING (auth.uid() = id);

-- DRIVERS: allow public SELECT (riders need to view nearby drivers), but only the driver can UPDATE their row
DROP POLICY IF EXISTS drivers_select_public ON public.drivers;
CREATE POLICY drivers_select_public ON public.drivers FOR SELECT USING (true);

DROP POLICY IF EXISTS drivers_update_self ON public.drivers;
CREATE POLICY drivers_update_self ON public.drivers FOR UPDATE USING (auth.uid() = id) WITH CHECK (auth.uid() = id);

-- Rides: riders and assigned drivers can SELECT; INSERT allowed for riders (rider_id must match auth.uid()); UPDATE allowed for rider or driver depending on context
DROP POLICY IF EXISTS rides_select_parties ON public.rides;
CREATE POLICY rides_select_parties ON public.rides FOR SELECT USING (rider_id = auth.uid() OR driver_id = auth.uid());

DROP POLICY IF EXISTS rides_insert_rider ON public.rides;
CREATE POLICY rides_insert_rider ON public.rides FOR INSERT WITH CHECK (rider_id = auth.uid());

DROP POLICY IF EXISTS rides_update_parties ON public.rides;
CREATE POLICY rides_update_parties ON public.rides FOR UPDATE USING (rider_id = auth.uid() OR driver_id = auth.uid()) WITH CHECK (rider_id = auth.uid() OR driver_id = auth.uid());

-- Allow service role (server) to bypass RLS via service role key (service role bypasses policies by default)

-- Add helpful function-based policies where needed (left simple for now)

-- End of RLS
