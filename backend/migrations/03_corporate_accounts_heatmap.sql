-- ============================================================
-- Corporate Accounts and Heat Map Settings Migration
-- Run this in the Supabase SQL Editor (Settings → SQL Editor)
-- ============================================================

-- Enable UUID extension (usually already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. CORPORATE_ACCOUNTS
-- ============================================================
CREATE TABLE IF NOT EXISTS corporate_accounts (
    id                  TEXT PRIMARY KEY,
    company_name        TEXT NOT NULL,
    contact_name        TEXT NOT NULL,
    contact_email       TEXT NOT NULL,
    contact_phone       TEXT NOT NULL,
    discount_percentage FLOAT NOT NULL DEFAULT 0,
    billing_address     TEXT,
    tax_id              TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_corporate_active ON corporate_accounts (is_active);

-- ============================================================
-- 2. Add corporate_account_id to users table
-- ============================================================
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS corporate_account_id TEXT REFERENCES corporate_accounts(id),
ADD COLUMN IF NOT EXISTS is_corporate_user BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_users_corporate ON users (corporate_account_id);

-- ============================================================
-- 3. Add corporate_account_id to rides table
-- ============================================================
ALTER TABLE rides 
ADD COLUMN IF NOT EXISTS corporate_account_id TEXT REFERENCES corporate_accounts(id);

CREATE INDEX IF NOT EXISTS idx_rides_corporate ON rides (corporate_account_id);
CREATE INDEX IF NOT EXISTS idx_rides_pickup_coords ON rides (pickup_lat, pickup_lng);
CREATE INDEX IF NOT EXISTS idx_rides_dropoff_coords ON rides (dropoff_lat, dropoff_lng);

-- ============================================================
-- 4. Add heat map settings to settings table
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

-- ============================================================
-- 5. Enable RLS on corporate_accounts table
-- ============================================================
ALTER TABLE corporate_accounts ENABLE ROW LEVEL SECURITY;

-- Allow admin full access
CREATE POLICY "Admin full access corporate_accounts" 
ON corporate_accounts FOR ALL 
TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM users 
    WHERE users.id = auth.uid()::text 
    AND users.role = 'admin'
  )
);

-- ============================================================
-- Seed some sample corporate accounts (for testing)
-- ============================================================
INSERT INTO corporate_accounts (id, company_name, contact_name, contact_email, contact_phone, discount_percentage, is_active)
SELECT 'corp_acme_001', 'Acme Corporation', 'John Smith', 'john@acme.com', '+1234567890', 10, TRUE
WHERE NOT EXISTS (SELECT 1 FROM corporate_accounts WHERE id = 'corp_acme_001');

INSERT INTO corporate_accounts (id, company_name, contact_name, contact_email, contact_phone, discount_percentage, is_active)
SELECT 'corp_tech_002', 'Tech Solutions Inc', 'Sarah Johnson', 'sarah@techsolutions.com', '+1234567891', 15, TRUE
WHERE NOT EXISTS (SELECT 1 FROM corporate_accounts WHERE id = 'corp_tech_002');
