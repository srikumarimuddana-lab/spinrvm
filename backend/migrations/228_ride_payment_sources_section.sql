-- 228_ride_payment_sources_section.sql
--
-- Settlement chain (Phase 5 / M5.5, owner Q9/Q13): corporate fare settlement
-- becomes  member allowance → member's SECTION wallet (floor 0) → master
-- wallet (down to its credit floor). ride_payment_sources — the per-ride
-- billing attribution row — gains the section leg so:
--   * billing/dashboards can attribute spend to departments, and
--   * refunds can reverse PER LEG to the wallet that paid (Q13).
--
--   * section_id           — the member's section at settlement time (frozen
--     here because corporate_members.section_id is mutable; the refund must
--     go back to the wallet that actually paid, not the member's CURRENT
--     section). TEXT-free UUID FK to corporate_sections, no CASCADE (billing
--     attribution must survive section archival; sections are archive-only).
--   * section_debit_amount — the section-wallet leg (NUMERIC, Decimal-only).
--
-- Forward-compatible: nullable metadata-only ADD COLUMNs; existing rows read
-- as "no section leg" (correct — the tier didn't exist). No new table → RLS
-- posture unchanged (ride_payment_sources RLS from migration 27).
--
-- Rollback (on paper):
--   ALTER TABLE ride_payment_sources
--     DROP COLUMN IF EXISTS section_id,
--     DROP COLUMN IF EXISTS section_debit_amount;

ALTER TABLE ride_payment_sources
    ADD COLUMN IF NOT EXISTS section_id           UUID REFERENCES corporate_sections(id),
    ADD COLUMN IF NOT EXISTS section_debit_amount NUMERIC(12,2) DEFAULT 0.00;

-- Dashboards/billing filter company spend by section (Phase 6).
CREATE INDEX IF NOT EXISTS ride_payment_sources_section_idx
    ON ride_payment_sources (section_id)
    WHERE section_id IS NOT NULL;
