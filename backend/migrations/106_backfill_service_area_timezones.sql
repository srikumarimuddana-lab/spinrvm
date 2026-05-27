-- Backfill service_areas.timezone from the province code so rows created before
-- migration 105 (which defaulted everything to America/Regina) get the correct
-- IANA timezone for their province.
--
-- Rollback: UPDATE service_areas SET timezone = 'America/Regina';
--
-- Only rows with a non-NULL province are touched; rows with province IS NULL
-- stay on the existing default (America/Regina) until an admin updates them.

UPDATE service_areas
SET timezone = CASE province
  WHEN 'SK' THEN 'America/Regina'
  WHEN 'AB' THEN 'America/Edmonton'
  WHEN 'MB' THEN 'America/Winnipeg'
  WHEN 'ON' THEN 'America/Toronto'
  WHEN 'BC' THEN 'America/Vancouver'
  WHEN 'QC' THEN 'America/Toronto'
  WHEN 'NS' THEN 'America/Halifax'
  WHEN 'NB' THEN 'America/Halifax'
  WHEN 'PE' THEN 'America/Halifax'
  WHEN 'NL' THEN 'America/St_Johns'
  WHEN 'NT' THEN 'America/Yellowknife'
  WHEN 'YT' THEN 'America/Whitehorse'
  WHEN 'NU' THEN 'America/Rankin_Inlet'
  ELSE timezone
END
WHERE province IS NOT NULL;
