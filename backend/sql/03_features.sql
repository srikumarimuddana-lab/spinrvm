-- ============================================
-- 03_features.sql  –  Extended feature tables
-- Run in Supabase SQL Editor
-- ============================================

-- ========== Support Tickets ==========
CREATE TABLE IF NOT EXISTS support_tickets (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID REFERENCES users(id),
  subject      TEXT NOT NULL,
  message      TEXT NOT NULL,
  category     TEXT DEFAULT 'general',        -- general | ride | payment | safety | driver
  status       TEXT DEFAULT 'open',           -- open | in_progress | closed
  replies      JSONB DEFAULT '[]'::jsonb,     -- [{message, created_at, author}]
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now()
);

-- ========== FAQs ==========
CREATE TABLE IF NOT EXISTS faqs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question     TEXT NOT NULL,
  answer       TEXT NOT NULL,
  category     TEXT DEFAULT 'general',
  sort_order   INTEGER DEFAULT 0,
  is_active    BOOLEAN DEFAULT true,
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now()
);

-- ========== Surge Pricing columns on service_areas ==========
-- Add surge columns to existing service_areas table
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS surge_active BOOLEAN DEFAULT false;
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS surge_multiplier NUMERIC(4,2) DEFAULT 1.0;

-- ========== Airport Zone columns on service_areas ==========
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS is_airport BOOLEAN DEFAULT false;
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS airport_fee NUMERIC(6,2) DEFAULT 0.00;

-- ========== Tax Configuration on service_areas ==========
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS gst_enabled BOOLEAN DEFAULT true;
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS gst_rate NUMERIC(5,3) DEFAULT 5.000;   -- 5% federal GST
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS pst_enabled BOOLEAN DEFAULT false;
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS pst_rate NUMERIC(5,3) DEFAULT 0.000;   -- provincial PST (SK=6%, BC=7%, etc.)
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS hst_enabled BOOLEAN DEFAULT false;      -- harmonized (ON=13%, etc.)
ALTER TABLE service_areas ADD COLUMN IF NOT EXISTS hst_rate NUMERIC(5,3) DEFAULT 0.000;

-- ========== Area Fees (per-area custom fee types) ==========
CREATE TABLE IF NOT EXISTS area_fees (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_area_id UUID REFERENCES service_areas(id) ON DELETE CASCADE,
  fee_name        TEXT NOT NULL,                   -- "Airport Surcharge", "Night Fee"
  fee_type        TEXT NOT NULL DEFAULT 'custom',  -- airport | night | toll | event | holiday | custom
  calc_mode       TEXT NOT NULL DEFAULT 'flat',    -- flat | per_km | percentage
  amount          NUMERIC(8,2) NOT NULL DEFAULT 0.00,   -- $ or % depending on calc_mode
  description     TEXT,
  conditions      JSONB DEFAULT '{}'::jsonb,       -- e.g. {"start_hour": 23, "end_hour": 5} for night
  is_active       BOOLEAN DEFAULT true,
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ========== Driver Area Restriction ==========
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS service_area_id UUID;

-- ========== Fee & tax tracking on rides ==========
ALTER TABLE rides ADD COLUMN IF NOT EXISTS airport_fee NUMERIC(6,2) DEFAULT 0.00;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS airport_zone_name TEXT;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS area_fees_total NUMERIC(8,2) DEFAULT 0.00;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS area_fees_breakdown JSONB DEFAULT '[]'::jsonb;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS tax_amount NUMERIC(8,2) DEFAULT 0.00;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS tax_breakdown JSONB DEFAULT '{}'::jsonb;

-- ========== Stripe Payment Tracking ==========
ALTER TABLE rides ADD COLUMN IF NOT EXISTS stripe_transaction_id TEXT;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT;

-- ========== Scheduled Rides + Multi-stop columns on rides ==========
ALTER TABLE rides ADD COLUMN IF NOT EXISTS scheduled_time TIMESTAMPTZ;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS is_scheduled BOOLEAN DEFAULT false;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS stops JSONB DEFAULT '[]'::jsonb;

-- ========== Safety Toolkit columns ==========
ALTER TABLE rides ADD COLUMN IF NOT EXISTS shared_trip_token TEXT;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS shared_trip_contacts JSONB DEFAULT '[]'::jsonb;

-- ========== Push Notification tokens ==========
ALTER TABLE users ADD COLUMN IF NOT EXISTS fcm_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_preferences JSONB DEFAULT '{"ride_updates": true, "promotions": true, "safety_alerts": true}'::jsonb;

-- ========== Driver Registration & Validation (Saskatchewan) ==========
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS license_number TEXT;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS license_expiry_date DATE;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS work_eligibility_expiry_date DATE;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS vehicle_year INTEGER;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS vehicle_vin TEXT;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS vehicle_inspection_expiry_date DATE;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS insurance_expiry_date DATE;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS background_check_expiry_date DATE;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS documents JSONB DEFAULT '{}'::jsonb; -- { "license_front": "url", ... }
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT false;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS rejection_reason TEXT;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_drivers_is_verified ON drivers(is_verified);
CREATE INDEX IF NOT EXISTS idx_drivers_submitted_at ON drivers(submitted_at);

-- Enable RLS (if not already enabled)
ALTER TABLE support_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE faqs ENABLE ROW LEVEL SECURITY;

-- Basic RLS policies
CREATE POLICY IF NOT EXISTS "Users can view own tickets" ON support_tickets
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY IF NOT EXISTS "Users can create own tickets" ON support_tickets
  FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY IF NOT EXISTS "Admins manage all tickets" ON support_tickets
  FOR ALL USING (auth.role() = 'service_role');

CREATE POLICY IF NOT EXISTS "Anyone can read active FAQs" ON faqs
  FOR SELECT USING (is_active = true);

CREATE POLICY IF NOT EXISTS "Admins manage FAQs" ON faqs
  FOR ALL USING (auth.role() = 'service_role');
