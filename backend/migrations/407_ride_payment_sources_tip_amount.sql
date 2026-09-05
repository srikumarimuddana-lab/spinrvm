-- 407_ride_payment_sources_tip_amount.sql
--
-- Adds ride_payment_sources.tip_amount -- the portion of
-- allowance_debit_amount + master_fallback_amount that was the rider's
-- personal tip on a corporate (company_allowance) ride, per #4074.
--
-- Context: #4074 found that a rider's tip on a corporate ride is billed to
-- the employer's allowance/master wallet with no separate line item -- it
-- was invisible inside the fare on company invoices/statements, and the
-- same tip-inclusive total was fed into corporate policy fare-cap
-- evaluation (a false-positive violation risk) and the low-allowance
-- notification. Product decision (2026-09-05): tips stay billed to the
-- company (matches the existing 2026-08-17 late-tip precedent, see
-- docs/change-log/2026-08-17-wallet-corporate-late-tip-debit.md) -- this
-- migration only fixes the REPORTING gap by giving the tip its own column
-- so it can be broken out on invoices/statements and excluded from policy
-- fare-cap evaluation (see the accompanying backend/services/
-- payment_service.py change). The debit amounts themselves are unchanged.
--
-- Additive, forward-compatible: existing rows default to 0.00 (correct --
-- every row written before this migration was fully described by the
-- existing allowance/master columns; there is no historical tip figure to
-- backfill separately since it was never split out). No index needed --
-- this column is never filtered/ordered on, only summed/displayed
-- alongside the existing allowance_debit_amount/master_fallback_amount
-- reads in routes/corporate_company.py and utils/corporate_statement_pdf.py.
--
-- Rollback: ALTER TABLE ride_payment_sources DROP COLUMN tip_amount;
-- (safe -- no other table/view depends on it as of this migration)

ALTER TABLE ride_payment_sources
    ADD COLUMN IF NOT EXISTS tip_amount NUMERIC(12,2) NOT NULL DEFAULT 0.00;

COMMENT ON COLUMN ride_payment_sources.tip_amount IS
    'Portion of allowance_debit_amount + master_fallback_amount that was the '
    'rider''s personal tip, billed to the company per the 2026-09-05 product '
    'decision on #4074. Kept separate for invoice/statement transparency and '
    'so policy fare-cap evaluation can exclude it. Not a distinct charge -- '
    'always <= allowance_debit_amount + master_fallback_amount.';

NOTIFY pgrst, 'reload schema';
