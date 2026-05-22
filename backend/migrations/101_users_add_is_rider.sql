-- Migration: 101_users_add_is_rider.sql
-- Rollback: ALTER TABLE users DROP COLUMN IF EXISTS is_rider;
--
-- Adds is_rider boolean flag to complement the existing is_driver flag,
-- enabling dual-role accounts (a person who both rides and drives).
--
-- Previously, users.role was a single-valued enum (rider | driver | admin).
-- Anyone who completed even partial driver onboarding had their role flipped
-- to 'driver', which excluded them from rider-targeted notifications and
-- features even if they still used the rider app.
--
-- With this migration:
--   is_rider = true  → user can book rides (receives "all customers" pushes)
--   is_driver = true → user has a driver profile and can accept rides
--   Both true        → dual-role (Uber-style); receives both sets of pushes
--
-- The role column is NOT dropped — it remains for admin JWT RBAC
-- (super_admin | operations | support | finance | custom | admin).
-- All rider/driver discrimination now uses these two boolean flags.

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_rider BOOLEAN NOT NULL DEFAULT TRUE;

-- Everyone can ride by default, including existing drivers.
UPDATE users SET is_rider = TRUE WHERE is_rider IS NULL OR is_rider = FALSE;
