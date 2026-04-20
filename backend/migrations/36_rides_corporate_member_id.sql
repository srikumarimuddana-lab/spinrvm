-- Migration 36: Add corporate_member_id to rides
--
-- Stores which corporate_members row was active at booking time so
-- process_payment() can debit the correct allowance without a second
-- lookup against corporate_members at completion time.
--
-- Nullable because existing rides and non-corporate rides have no member.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS corporate_member_id UUID REFERENCES corporate_members(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_rides_corporate_member
    ON rides (corporate_member_id)
    WHERE corporate_member_id IS NOT NULL;
