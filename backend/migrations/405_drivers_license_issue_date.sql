-- 405_drivers_license_issue_date.sql
-- Purpose: Add the schema field needed to enforce CLAUDE.md's Saskatchewan
-- Regulatory "minimum 3 years licensed driving experience" driver-eligibility
-- rule. ACTION_ITEMS.md C63 (split out of A40 ranked blocker #10 /
-- docs/change-log/2026-08-19-go-online-sk-eligibility-recheck-fix.md, which
-- shipped the license-class and vehicle-age sub-checks but explicitly left
-- this third sub-check unimplemented because no field existed to check
-- against).
--
-- Rollback:
--   DROP INDEX IF EXISTS public.idx_drivers_license_issue_date;
--   ALTER TABLE public.drivers DROP COLUMN IF EXISTS license_issue_date;
--
-- Notes:
-- - Nullable, additive column — safe with production traffic in flight, no
--   table rewrite (single ADD COLUMN with no DEFAULT computation).
-- - Named to match the existing `license_expiry_date` (08_complete_schema.sql)
--   column already on this table, not the `license_issued_date` spelling
--   floated in the 2026-08-19 change log's follow-up note.
-- - Deliberately NOT backfilled here. There is no reliable source to derive
--   an issue date from for already-onboarded drivers (onboarding document
--   metadata was never captured in a structured column — only in per-document
--   rows/files, if at all), and per this migration's own field comment and
--   the go_online recheck's fail-safe design (see routes/drivers/status.py),
--   a NULL value is read as "unknown, do not block" rather than "fails the
--   3-year check". Guessing a value here would risk the opposite failure
--   mode: wrongly locking out an already-active driver. See the B14/22-driver
--   precedent cited in the 2026-08-19 change log for why an unverified
--   backfill is worse than leaving the column NULL.
-- - No RLS policy changes needed: `drivers` already has its existing RLS
--   policies; this is an additive column on an existing table, not a new one.

ALTER TABLE public.drivers
  ADD COLUMN IF NOT EXISTS license_issue_date DATE;

-- Partial index: only drivers with a known issue date are ever looked up by
-- this column (the go_online recheck skips NULLs entirely), matching the
-- idx_drivers_license_class/idx_drivers_sgi_approved partial-index pattern
-- from migration 221.
CREATE INDEX IF NOT EXISTS idx_drivers_license_issue_date
  ON public.drivers (license_issue_date)
  WHERE license_issue_date IS NOT NULL;

COMMENT ON COLUMN public.drivers.license_issue_date IS
  'Date the driver''s licence was first issued, used to derive licensed '
  'driving experience for the Saskatchewan "minimum 3 years licensed '
  'driving experience" eligibility rule (CLAUDE.md Saskatchewan Regulatory). '
  'NULL for any driver whose issue date is not on file (all drivers '
  'onboarded before this column existed, and any driver onboarded after '
  'without capturing it) — the go_online eligibility recheck in '
  'routes/drivers/status.py treats NULL as "unknown, do not block" rather '
  'than as a failure, so this column is intentionally left un-backfilled. '
  'See migration 405 header and ACTION_ITEMS.md C63.';
