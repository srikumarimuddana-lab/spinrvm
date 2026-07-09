-- 222_drivers_general_regulatory_approval.sql
-- Purpose: Generalize Saskatchewan-only SGI approval import fields so future
-- Alberta/Ontario/other-province launches can track the applicable regulator
-- or municipal licensing authority without adding province-specific columns.
--
-- Rollback:
--   ALTER TABLE public.drivers
--     DROP COLUMN IF EXISTS regulatory_authority,
--     DROP COLUMN IF EXISTS regulatory_region,
--     DROP COLUMN IF EXISTS regulatory_authority_approved,
--     DROP COLUMN IF EXISTS regulatory_authority_approved_at;
--   DROP INDEX IF EXISTS idx_drivers_regulatory_authority_approved;
--   DROP INDEX IF EXISTS idx_drivers_regulatory_region;
--
-- Notes:
-- - Nullable/additive for production safety.
-- - Existing sgi_approved values are copied forward for Saskatchewan imports.
-- - Keep the old SGI columns for backward compatibility with any in-flight PRs
--   or exports; new code should read/write the generalized columns.

ALTER TABLE public.drivers
  ADD COLUMN IF NOT EXISTS regulatory_authority TEXT,
  ADD COLUMN IF NOT EXISTS regulatory_region TEXT,
  ADD COLUMN IF NOT EXISTS regulatory_authority_approved BOOLEAN,
  ADD COLUMN IF NOT EXISTS regulatory_authority_approved_at TIMESTAMPTZ;

UPDATE public.drivers
SET
  regulatory_authority = COALESCE(regulatory_authority, 'SGI'),
  regulatory_region = COALESCE(regulatory_region, 'SK'),
  regulatory_authority_approved = COALESCE(regulatory_authority_approved, sgi_approved),
  regulatory_authority_approved_at = COALESCE(regulatory_authority_approved_at, sgi_approved_at)
WHERE sgi_approved IS NOT NULL
   OR sgi_approved_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_drivers_regulatory_authority_approved
  ON public.drivers (regulatory_authority_approved)
  WHERE regulatory_authority_approved IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_drivers_regulatory_region
  ON public.drivers (regulatory_region)
  WHERE regulatory_region IS NOT NULL;

COMMENT ON COLUMN public.drivers.regulatory_authority IS
  'Province/municipal regulator or licensing authority label for imported approval data, e.g. SGI, Alberta TNC/municipal licence, Toronto PTC.';
COMMENT ON COLUMN public.drivers.regulatory_region IS
  'Regulatory region for approval metadata, e.g. SK, AB, ON, Toronto, Calgary.';
COMMENT ON COLUMN public.drivers.regulatory_authority_approved IS
  'Whether the relevant provincial/municipal authority approval or eligibility check is recorded as approved.';
COMMENT ON COLUMN public.drivers.regulatory_authority_approved_at IS
  'Timestamp when the regulatory approval was verified/imported, if known.';
