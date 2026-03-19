-- ============================================================
-- CORPORATE ACCOUNTS SCHEMA
-- Run this in Supabase SQL Editor to create corporate accounts table
-- ============================================================

-- Enable UUID extension (usually already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create the update_updated_at_column function if it doesn't exist
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- ============================================================
-- 13. CORPORATE ACCOUNTS
-- ============================================================
CREATE TABLE IF NOT EXISTS corporate_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,
    credit_limit    NUMERIC DEFAULT 0,
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_corporate_accounts_name ON corporate_accounts(name);
CREATE INDEX IF NOT EXISTS idx_corporate_accounts_contact_email ON corporate_accounts(contact_email);
CREATE INDEX IF NOT EXISTS idx_corporate_accounts_is_active ON corporate_accounts(is_active);

-- Update updated_at trigger (reuse existing function)
DROP TRIGGER IF EXISTS update_corporate_accounts_updated_at ON corporate_accounts;
CREATE TRIGGER update_corporate_accounts_updated_at
BEFORE UPDATE ON corporate_accounts
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- Enable RLS
ALTER TABLE corporate_accounts ENABLE ROW LEVEL SECURITY;

-- Policies for corporate_accounts (admin only access)
CREATE POLICY "Admin full access for corporate accounts"
ON corporate_accounts FOR ALL
TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM public.users 
    WHERE public.users.id = auth.uid()::text 
    AND public.users.role = 'admin'
  )
);

-- ============================================================
-- Test data (optional - remove if not needed)
-- ============================================================
-- INSERT INTO corporate_accounts (name, contact_name, contact_email, contact_phone, credit_limit, is_active)
-- VALUES 
--   ('Acme Corp', 'John Doe', 'john@acme.com', '+1-555-0123', 1000.00, true),
--   ('Tech Solutions Inc', 'Jane Smith', 'jane@techsol.com', '+1-555-0124', 2500.00, true),
--   ('Global Enterprises', 'Bob Wilson', 'bob@global.com', '+1-555-0125', 5000.00, false);