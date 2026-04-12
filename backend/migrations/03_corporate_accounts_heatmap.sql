-- ============================================================
-- Heat Map Settings + Corporate Account Link Columns
-- Run this in the Supabase SQL Editor (Settings → SQL Editor)
-- ============================================================
--
-- NOTE: Historically this file also created the `corporate_accounts`
-- table with an incompatible shape (TEXT id, company_name, etc.).
-- The canonical `corporate_accounts` definition now lives in
-- `migrations/05_corporate_accounts.sql` (UUID id, name, credit_limit),
-- which matches the CorporateAccount Pydantic models in
-- `routes/corporate_accounts.py`. The CREATE TABLE, seed INSERTs, and
-- RLS for corporate_accounts have been removed from this file.
--
-- The FK link columns on `users` and `rides` are added here WITHOUT a
-- REFERENCES clause (because 05 hasn't run yet at this point in the
-- migration chain). The foreign-key constraints are added by
-- `migrations/17_corporate_accounts_fk.sql` after 05 creates the table.

-- ============================================================
-- 1. Add corporate_account_id + is_corporate_user to users
-- ============================================================
ALTER TABLE users
ADD COLUMN IF NOT EXISTS corporate_account_id UUID,
ADD COLUMN IF NOT EXISTS is_corporate_user BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_users_corporate ON users (corporate_account_id);

-- ============================================================
-- 2. Add corporate_account_id to rides
-- ============================================================
ALTER TABLE rides
ADD COLUMN IF NOT EXISTS corporate_account_id UUID;

CREATE INDEX IF NOT EXISTS idx_rides_corporate ON rides (corporate_account_id);
CREATE INDEX IF NOT EXISTS idx_rides_pickup_coords ON rides (pickup_lat, pickup_lng);
CREATE INDEX IF NOT EXISTS idx_rides_dropoff_coords ON rides (dropoff_lat, dropoff_lng);

-- ============================================================
-- 3. Add heat map settings to the settings table
-- ============================================================
ALTER TABLE settings
ADD COLUMN IF NOT EXISTS heat_map_enabled BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS heat_map_default_range TEXT DEFAULT '30d',
ADD COLUMN IF NOT EXISTS heat_map_intensity TEXT DEFAULT 'medium',
ADD COLUMN IF NOT EXISTS heat_map_radius INTEGER DEFAULT 25,
ADD COLUMN IF NOT EXISTS heat_map_blur INTEGER DEFAULT 15,
ADD COLUMN IF NOT EXISTS heat_map_gradient_start TEXT DEFAULT '#00ff00',
ADD COLUMN IF NOT EXISTS heat_map_gradient_mid TEXT DEFAULT '#ffff00',
ADD COLUMN IF NOT EXISTS heat_map_gradient_end TEXT DEFAULT '#ff0000',
ADD COLUMN IF NOT EXISTS heat_map_show_pickups BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS heat_map_show_dropoffs BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS corporate_heat_map_enabled BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS regular_rider_heat_map_enabled BOOLEAN DEFAULT TRUE;
