-- 229_credit_floor_opt_in.sql
--
-- Extended credit is OPT-IN (owner decision, 2026-07-10, refining Q10):
-- M5.5 engages corporate_wallets.soft_negative_floor at fare settlement —
-- previously the settlement fallback used floor=0.0, so the column's -50.00
-- default was dormant. Left as-is, shipping M5.5 would silently hand EVERY
-- company an automatic $50 credit line on the ride-settlement path (flagged
-- by the money audit as unapproved credit exposure).
--
--   * Column default becomes 0.00 — a new company has NO credit until Spinr
--     staff explicitly set a credit floor for it.
--   * Existing rows still sitting on the old -50.00 default are reset to 0.
--     Any other value was deliberately staff-set and is preserved.
--
-- Forward-compatible: default change is metadata-only; the UPDATE touches
-- only rows at exactly -50.00 (the untouched default) on a small B2B table.
--
-- Rollback (on paper):
--   ALTER TABLE corporate_wallets ALTER COLUMN soft_negative_floor SET DEFAULT -50.00;
--   (Per-row values are NOT restorable — the -50 rows were defaults, not data.)

ALTER TABLE corporate_wallets
    ALTER COLUMN soft_negative_floor SET DEFAULT 0.00;

UPDATE corporate_wallets
SET soft_negative_floor = 0.00
WHERE soft_negative_floor = -50.00;
