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
-- polluted in production.
--
-- Design notes:
--   * Per-field gating: each column is only rewritten when IT holds the
--     'Driver' placeholder (d.first_name = 'Driver' / d.name = 'Driver'). A row
--     matched on one field never clobbers a legitimate value on another field.
--   * When first_name is recovered from the account, last_name is adopted from
--     the same account row so the two stay consistent.
--   * LEFT JOIN (via a drivers self-join) so rows with a NULL/unmatched
--     user_id (drivers.user_id is nullable) are still cleaned — the u.* refs
--     are all null-safe and fall through to the phone, then the id.
--   * The display `name` never falls back to the 'Driver' literal again: the
--     final COALESCE arm is d.id, so a matched row always leaves with a
--     non-placeholder name.
--
-- Idempotent: re-running only matches rows whose name/first_name is still the
-- 'Driver' placeholder, so already-cleaned rows are skipped.
--
-- Scope/lock: single UPDATE, no DDL. The bug is recent and affects a small
-- number of rows; if a future run finds many, confirm the count first
--   SELECT count(*) FROM drivers WHERE name = 'Driver' OR first_name = 'Driver';
-- and batch by id range before applying against live traffic.
--
-- Rollback plan: none required — this only replaces the 'Driver' placeholder
-- with the real account name, the phone, or the id. The placeholder carried no
-- data, so there is nothing to reverse.

UPDATE drivers d
SET
    first_name = CASE
        WHEN d.first_name = 'Driver' THEN NULLIF(TRIM(u.first_name), '')
        ELSE d.first_name
    END,
    last_name = CASE
        WHEN d.first_name = 'Driver' AND NULLIF(TRIM(u.first_name), '') IS NOT NULL
            THEN NULLIF(TRIM(u.last_name), '')
        WHEN d.last_name = 'Driver' THEN NULL
        ELSE d.last_name
    END,
    name = CASE
        WHEN d.name = 'Driver' THEN COALESCE(
            NULLIF(TRIM(CONCAT_WS(' ', NULLIF(TRIM(u.first_name), ''), NULLIF(TRIM(u.last_name), ''))), ''),
            NULLIF(TRIM(d.phone), ''),
            NULLIF(TRIM(u.phone), ''),
            d.id
        )
        ELSE d.name
    END
FROM drivers d2
LEFT JOIN users u ON d2.user_id = u.id
WHERE d.id = d2.id
  AND (d.name = 'Driver' OR d.first_name = 'Driver');
