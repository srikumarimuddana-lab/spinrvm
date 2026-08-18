-- 316_service_areas_safety_authority.sql
--
-- Purpose: per-service-area safety contacts for the rider/driver Safety panel.
--
-- Why these are NOT the existing regulatory_* columns (migration 223):
-- `regulatory_authority` / `regulatory_region` / `regulatory_requirements_url`
-- hold DRIVER LICENSING metadata -- "who licenses drivers here" (SGI, Calgary
-- Livery Transport Services, Toronto PTC). They are read by
-- services/driver_import_service.py and the admin drivers page, they carry no
-- phone number, and their meaning is "who approves this driver", not "who does
-- a rider contact". Reusing them would repurpose a column's meaning without a
-- migration + dual-read window, which CLAUDE.md's release gates forbid. New,
-- additive columns instead.
--
-- Scope note: only the LOCAL AUTHORITY genuinely varies by city, so only it
-- lives here. The Spinr safety-team email/phone and the panel's tile toggles
-- are global and live in `app_settings` -- putting them per-area would mean
-- editing every row to change one address.
--
-- Real-world shape this has to support:
--   Calgary  -> City of Calgary 311 is the designated rideshare complaint
--               channel under Livery Transport Bylaw 20M2021. Fully populated.
--   Regina / Saskatoon -> regulated provincially (SGI). Name + URL, NO phone,
--               because no municipal rideshare hotline exists. The panel
--               renders a link with no call button.
--   A brand-new area -> all NULL. The panel hides the row entirely.
--
-- IMPORTANT for whoever wires validation: safety_authority_phone must accept
-- 3-DIGIT SERVICE CODES (311, 211, 811). A naive E.164 regex would reject the
-- single most important value this column will ever hold.
--
-- emergency_number is stored rather than hardcoded so a future non-911
-- jurisdiction is a data change, not a release. It is NOT a way to redirect
-- 911 -- see the admin-side note; changing it is a safety-critical edit.
--
-- Rollback:
--   ALTER TABLE public.service_areas
--     DROP COLUMN IF EXISTS emergency_number,
--     DROP COLUMN IF EXISTS safety_authority_name,
--     DROP COLUMN IF EXISTS safety_authority_phone,
--     DROP COLUMN IF EXISTS safety_authority_url,
--     DROP COLUMN IF EXISTS safety_authority_hours;
--   Safe at any time: all nullable, nothing joins on them, and every consumer
--   treats absence as "hide the row" (pre-migration behavior).

ALTER TABLE public.service_areas
  ADD COLUMN IF NOT EXISTS emergency_number        TEXT DEFAULT '911',
  ADD COLUMN IF NOT EXISTS safety_authority_name   TEXT,
  ADD COLUMN IF NOT EXISTS safety_authority_phone  TEXT,
  ADD COLUMN IF NOT EXISTS safety_authority_url    TEXT,
  ADD COLUMN IF NOT EXISTS safety_authority_hours  TEXT;

-- Backfill the emergency number for existing rows. Every current service area
-- is Canadian, where 911 is universal.
UPDATE public.service_areas
SET emergency_number = '911'
WHERE emergency_number IS NULL;

COMMENT ON COLUMN public.service_areas.emergency_number IS
  'Emergency services number dialled by the Safety panel. Defaults to 911. '
  'Stored per-area so a non-911 jurisdiction is a config change, not a release. '
  'Safety-critical: an incorrect value sends someone in danger to the wrong place.';
COMMENT ON COLUMN public.service_areas.safety_authority_name IS
  'Local NON-EMERGENCY transport authority shown in the Safety panel, e.g. '
  '"City of Calgary 311" or "SGI". NULL hides the row entirely.';
COMMENT ON COLUMN public.service_areas.safety_authority_phone IS
  'Optional. May be a 3-digit service code such as 311 -- do not validate as '
  'E.164. NULL renders the row as an informational link with no call button.';
COMMENT ON COLUMN public.service_areas.safety_authority_url IS
  'Admin-maintained link to the authority''s rideshare complaint/info page.';
COMMENT ON COLUMN public.service_areas.safety_authority_hours IS
  'Free text, e.g. "24/7" or "Mon-Fri 8:00-16:00 CST". Display only.';
