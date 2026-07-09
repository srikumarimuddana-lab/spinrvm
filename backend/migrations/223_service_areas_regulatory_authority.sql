-- 223_service_areas_regulatory_authority.sql
-- Purpose: Store per-service-area regulatory authority defaults so imports and
-- admin driver edits can use the correct provincial/municipal regulator instead
-- of hardcoding Saskatchewan SGI.
--
-- Rollback:
--   ALTER TABLE public.service_areas
--     DROP COLUMN IF EXISTS regulatory_authority,
--     DROP COLUMN IF EXISTS regulatory_region,
--     DROP COLUMN IF EXISTS regulatory_requirements_url,
--     DROP COLUMN IF EXISTS regulatory_notes;
--   DROP INDEX IF EXISTS idx_service_areas_regulatory_region;

ALTER TABLE public.service_areas
  ADD COLUMN IF NOT EXISTS regulatory_authority TEXT,
  ADD COLUMN IF NOT EXISTS regulatory_region TEXT,
  ADD COLUMN IF NOT EXISTS regulatory_requirements_url TEXT,
  ADD COLUMN IF NOT EXISTS regulatory_notes TEXT;

UPDATE public.service_areas
SET
  regulatory_authority = COALESCE(regulatory_authority, CASE WHEN province = 'SK' THEN 'SGI' ELSE NULL END),
  regulatory_region = COALESCE(regulatory_region, province)
WHERE regulatory_authority IS NULL
   OR regulatory_region IS NULL;

CREATE INDEX IF NOT EXISTS idx_service_areas_regulatory_region
  ON public.service_areas (regulatory_region)
  WHERE regulatory_region IS NOT NULL;

COMMENT ON COLUMN public.service_areas.regulatory_authority IS
  'Default regulator/licensing authority for drivers in this service area, e.g. SGI, Calgary Livery Transport Services, Toronto PTC.';
COMMENT ON COLUMN public.service_areas.regulatory_region IS
  'Province/municipality code or name for driver regulatory rules, e.g. SK, AB, ON, Calgary, Toronto.';
COMMENT ON COLUMN public.service_areas.regulatory_requirements_url IS
  'Admin-maintained source URL for local driver eligibility/licensing requirements.';
COMMENT ON COLUMN public.service_areas.regulatory_notes IS
  'Admin-maintained notes summarizing local regulatory requirements for this service area.';
