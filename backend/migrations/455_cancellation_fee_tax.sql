-- 455: Record GST/PST charged on a cancellation / no-show fee.
--
-- WHY
-- ---
-- Cancellation and no-show fees (services/cancellation_service.py) had zero
-- tax handling, and the rides row only carries the fee split
-- (cancellation_fee_admin / cancellation_fee_driver). When the new
-- app_settings flag `cancellation_fee_tax_enabled` is on, the cancel paths
-- compute GST/PST on the fee (reusing features.calculate_all_fees' tax
-- logic) and charge fee + tax. These two columns persist that tax so the
-- receipt surfaces (JSON /receipt, receipt PDF, email) can render the
-- ACTUAL charge instead of the stale pre-trip quote.
--
-- Additive by design (CLAUDE.md pre-merge gate 2): the existing pre-trip
-- quote columns (total_fare, grand_total, tax_amount, tax_breakdown, ...)
-- are NOT overwritten on cancel — other readers (analytics, the
-- captured-refund reconciliation script, admin views) may still expect the
-- original quote there. cancellation_fee_admin / cancellation_fee_driver
-- keep their existing pre-tax meaning (driver statements, auto-payout and
-- earnings read cancellation_fee_driver as the driver's payout).
--
-- WHAT
-- ----
-- rides.cancellation_fee_tax_amount    NUMERIC(10,2) NULL — total tax on the fee
-- rides.cancellation_fee_tax_breakdown JSONB NULL — {"GST": {"rate": 5.0, "amount": 0.23}, ...},
--                                      same shape as rides.tax_breakdown
--
-- Both nullable with no default: every historical cancelled ride reads as
-- "no tax recorded on the fee", which is exactly what happened to it.
--
-- Deploy order: this migration MUST be applied before
-- `cancellation_fee_tax_enabled` is flipped on. The backend only writes
-- these columns when tax > 0 (flag on), and that write is a separate
-- best-effort update that logs an error rather than failing the cancel.
--
-- SAFE TO RE-RUN: ADD COLUMN IF NOT EXISTS. No backfill, no table rewrite
-- (nullable, no default), no index needed (never filtered on).
--
-- Rollback: turn `cancellation_fee_tax_enabled` off first. The two columns
-- can then be left in place harmlessly (nothing reads them when NULL); if
-- they must be removed, remove both columns from rides by hand after
-- confirming no cancelled ride relies on them for its receipt.
--
-- Run-time estimate: two metadata-only ADD COLUMNs, well under the 30s SLA.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancellation_fee_tax_amount NUMERIC(10,2);

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancellation_fee_tax_breakdown JSONB;
