-- 234_backfill_driver_placeholder_name.sql
--
-- DATA FIX: some driver rows were created with the literal placeholder name
-- "Driver". register_driver used `full_name = ... or "Driver"` and then split
-- that fallback into first_name = "Driver" / last_name = NULL, so brand-new
-- drivers showed up in the admin panel literally named "Driver" instead of
-- their real name (see public.drivers rows with name = 'Driver').
--
-- The write path is fixed in code (routes/drivers/profile.py::register_driver
-- no longer splits the placeholder into first_name, and prefers the account's
-- users.first_name/last_name). This migration cleans up the rows already
-- polluted in production:
--   * If the linked users row has a real name, adopt it (first/last + name).
--   * Otherwise drop the "Driver" placeholder: null the first_name, and set the
--     display name to the phone number (what the other auto-create paths use),
--     falling back to the existing value only if there is no phone.
--
-- Idempotent: re-running only matches rows whose name/first_name is still the
-- 'Driver' placeholder, so already-cleaned rows are skipped.
--
-- Rollback plan: none required — this only replaces a placeholder with the
-- real account name or the phone. There is no information loss to reverse (the
-- "Driver" literal carried no data).

UPDATE drivers d
SET
    first_name = CASE
        WHEN NULLIF(TRIM(u.first_name), '') IS NOT NULL THEN u.first_name
        ELSE NULL
    END,
    last_name = CASE
        WHEN NULLIF(TRIM(u.first_name), '') IS NOT NULL THEN NULLIF(TRIM(u.last_name), '')
        WHEN d.last_name = 'Driver' THEN NULL
        ELSE d.last_name
    END,
    name = COALESCE(
        NULLIF(TRIM(CONCAT_WS(' ', NULLIF(TRIM(u.first_name), ''), NULLIF(TRIM(u.last_name), ''))), ''),
        NULLIF(TRIM(d.phone), ''),
        NULLIF(TRIM(u.phone), ''),
        d.name
    )
FROM users u
WHERE d.user_id = u.id
  AND (d.name = 'Driver' OR d.first_name = 'Driver');
