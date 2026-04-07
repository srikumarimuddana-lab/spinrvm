-- ============================================================
-- Add missing columns to promotions table
-- Run this in the Supabase SQL Editor
-- ============================================================

-- Add columns that the backend code uses but are not in the original schema
ALTER TABLE promotions
ADD COLUMN IF NOT EXISTS assigned_user_ids JSONB DEFAULT '[]'::jsonb;

ALTER TABLE promotions
ADD COLUMN IF NOT EXISTS inactive_days INTEGER DEFAULT 0;

ALTER TABLE promotions
ADD COLUMN IF NOT EXISTS min_total_rides INTEGER DEFAULT 0;

ALTER TABLE promotions
ADD COLUMN IF NOT EXISTS max_total_rides INTEGER DEFAULT 0;

-- Index for private coupon lookups
CREATE INDEX IF NOT EXISTS idx_promotions_promo_type ON promotions(promo_type);
