-- 221_drivers_bulk_import_fields.sql
-- Purpose: Add optional metadata fields needed for legacy Saskatoon driver imports.
-- Rollback:
--   ALTER TABLE public.drivers
--     DROP COLUMN IF EXISTS date_of_birth,
--     DROP COLUMN IF EXISTS license_class,
--     DROP COLUMN IF EXISTS sgi_approved,
--     DROP COLUMN IF EXISTS sgi_approved_at,
--     DROP COLUMN IF EXISTS work_authorization_status,
--     DROP COLUMN IF EXISTS is_permanent_resident,
--     DROP COLUMN IF EXISTS is_citizen,
--     DROP COLUMN IF EXISTS decals_sent,
--     DROP COLUMN IF EXISTS decals_sent_at,
--     DROP COLUMN IF EXISTS legacy_import_metadata;
--
-- Notes:
-- - All columns are nullable/additive so this is safe with production traffic.
-- - Full driver addresses are intentionally kept out of first-class plain-text
--   columns. Importers should keep them in legacy_import_metadata only if a
--   secure operational process has approved that data flow, or migrate them to
--   a vault-backed field in a follow-up.

ALTER TABLE public.drivers
  ADD COLUMN IF NOT EXISTS date_of_birth DATE,
  ADD COLUMN IF NOT EXISTS license_class TEXT,
  ADD COLUMN IF NOT EXISTS sgi_approved BOOLEAN,
  ADD COLUMN IF NOT EXISTS sgi_approved_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS work_authorization_status TEXT,
  ADD COLUMN IF NOT EXISTS is_permanent_resident BOOLEAN,
  ADD COLUMN IF NOT EXISTS is_citizen BOOLEAN,
  ADD COLUMN IF NOT EXISTS decals_sent BOOLEAN,
  ADD COLUMN IF NOT EXISTS decals_sent_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS legacy_import_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_drivers_sgi_approved
  ON public.drivers (sgi_approved)
  WHERE sgi_approved IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_drivers_license_class
  ON public.drivers (license_class)
  WHERE license_class IS NOT NULL;

COMMENT ON COLUMN public.drivers.date_of_birth IS
  'Driver date of birth imported from legacy onboarding data. PII: never log or expose outside admin/compliance flows.';
COMMENT ON COLUMN public.drivers.license_class IS
  'Saskatchewan driver licence class from onboarding/import data, e.g. 4 or 5.';
COMMENT ON COLUMN public.drivers.sgi_approved IS
  'Whether imported records indicate SGI approval for passenger-for-hire/rideshare operation.';
COMMENT ON COLUMN public.drivers.sgi_approved_at IS
  'Timestamp when SGI approval was verified/imported, if known.';
COMMENT ON COLUMN public.drivers.work_authorization_status IS
  'Normalized work authorization status such as expiring, indefinite, permanent_resident, citizen, unknown.';
COMMENT ON COLUMN public.drivers.is_permanent_resident IS
  'Imported immigration/work-eligibility flag. Sensitive admin/compliance data.';
COMMENT ON COLUMN public.drivers.is_citizen IS
  'Imported citizenship/work-eligibility flag. Sensitive admin/compliance data.';
COMMENT ON COLUMN public.drivers.decals_sent IS
  'Operations flag indicating whether Spinr decals were sent to the driver.';
COMMENT ON COLUMN public.drivers.decals_sent_at IS
  'Timestamp when decals were sent, if known.';
COMMENT ON COLUMN public.drivers.legacy_import_metadata IS
  'JSON metadata from legacy imports, scoped to admin/compliance use only. Avoid storing raw PII unless explicitly approved.';
