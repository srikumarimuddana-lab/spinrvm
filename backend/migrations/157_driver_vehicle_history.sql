-- Migration 157: driver_vehicle_history (append-only change log)
--
-- Driver vehicle/identity fields (make, model, colour, year, VIN, license
-- plate, vehicle type) are edited in place on the drivers table by both the
-- driver (self-service) and admins. For a regulated ride-share, the
-- vehicle/plate in effect at a given time is an SGI/insurance audit concern,
-- so we keep an append-only before/after history of every change.
--
-- Append-only: rows are inserted, never updated/deleted. Service-role only
-- (RLS enabled, no policies) — the backend writes it; the frontend anon key
-- must never touch it.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.driver_vehicle_history;

CREATE TABLE IF NOT EXISTS public.driver_vehicle_history (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id          UUID        NOT NULL,
    changed_by_user_id UUID,                                  -- who made the change (driver or admin); NULL for system
    changed_by_role    TEXT        NOT NULL DEFAULT 'system'  -- 'driver' | 'admin' | 'system'
                       CHECK (changed_by_role IN ('driver', 'admin', 'system')),
    field              TEXT        NOT NULL,                   -- e.g. 'license_plate', 'vehicle_make'
    old_value          TEXT,
    new_value          TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "Change timeline for this driver", newest first.
CREATE INDEX IF NOT EXISTS driver_vehicle_history_driver_idx
    ON public.driver_vehicle_history (driver_id, created_at DESC);

ALTER TABLE public.driver_vehicle_history ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.driver_vehicle_history IS
    'Append-only before/after log of driver vehicle/identity field changes (plate, make, model, etc.) for SGI/insurance audit. Service-role-only (RLS enabled, no policies).';
