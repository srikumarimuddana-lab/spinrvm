-- Backfill surge_enabled for service areas that already had surge configured.
--
-- Migration 81 added surge_enabled as NOT NULL DEFAULT FALSE with no backfill.
-- Fare pricing (fare_service.py, routes/fares.py, features.py fare-estimate)
-- and the surge engine now gate on surge_enabled, so every deployed area would
-- silently drop to 1.0x — losing active/manual surge pricing — until an admin
-- manually re-saves each one. Restore the prior pricing intent by enabling the
-- gate wherever surge was already active or a multiplier was parked above 1.0.
--
-- Safe under live traffic: a single bounded UPDATE on a small table. The fare
-- builders also require surge_active, so enabling a parked-multiplier-but-
-- inactive area does not start surging it; it only restores the operator's
-- saved configuration so a later activation behaves as it did before the gate.
--
-- Idempotent: re-running only re-sets rows already at TRUE. No-op on areas the
-- operator never configured for surge (the intended default-off behaviour).
--
-- Rollback: no down-migration. To revert the backfill specifically:
--   UPDATE service_areas SET surge_enabled = FALSE
--   WHERE surge_active = TRUE OR surge_multiplier > 1.0;
-- (only safe if no admin has toggled surge_enabled since this ran).

UPDATE service_areas
SET surge_enabled = TRUE
WHERE surge_enabled = FALSE
  AND (surge_active = TRUE OR surge_multiplier > 1.0);
