-- 81_service_areas_missing_columns.sql
--
-- Adds columns to `service_areas` that the admin update endpoint
-- writes to but were never created by a migration.
--
-- Symptoms before this migration:
--   - PUT /admin/service-areas/{id} returns 503 "database operation failed"
--     whenever the admin Save Changes button is clicked (sends the full form
--     including `show_demand_heatmap` and `required_documents`).
--   - Updating a polygon via the geofence map fails because GeneralTabForm
--     bundles the polygon with the full form payload.
--
-- Each column matches the field name used by:
--   - frontend: admin-dashboard/src/app/dashboard/service-areas/page.tsx (GeneralTabForm)
--   - backend:  backend/routes/admin/service_areas.py (allowlist in admin_update_service_area)
--
-- Rollback: ALTER TABLE service_areas DROP COLUMN IF EXISTS <col>;
-- Each column is independent — no FK or trigger references them.
--
-- Forward-compatible: all columns are nullable with safe defaults.
-- Production rows get the default value on column creation (instant for
-- non-volatile defaults like NULL / static booleans).

DO $$ BEGIN
    -- Per-area dashboard toggle for the rider demand heatmap overlay.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'show_demand_heatmap'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN show_demand_heatmap BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;

    -- Per-area required driver documents (license, abstract, etc.) as JSONB.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'required_documents'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN required_documents JSONB DEFAULT '[]'::JSONB;
    END IF;

    -- Per-area max pickup radius (km) for dispatch.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'max_pickup_radius_km'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN max_pickup_radius_km FLOAT NOT NULL DEFAULT 5.0;
    END IF;

    -- Province code (SK, AB, etc.) — frontend lets operator pick during create.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'province'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN province TEXT;
    END IF;

    -- Cancellation fee fields used by the Cancellation tab in the admin UI.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'rider_cancel_fee_before_driver'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN rider_cancel_fee_before_driver FLOAT NOT NULL DEFAULT 0;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'rider_cancel_fee_after_arrival'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN rider_cancel_fee_after_arrival FLOAT NOT NULL DEFAULT 4.50;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'cancel_fee_driver_share'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN cancel_fee_driver_share FLOAT NOT NULL DEFAULT 4.00;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'cancel_fee_admin_share'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN cancel_fee_admin_share FLOAT NOT NULL DEFAULT 0.50;
    END IF;

    -- Driver-initiated cancellation penalty.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'driver_cancel_fee'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN driver_cancel_fee FLOAT NOT NULL DEFAULT 0;
    END IF;

    -- Insurance fee percent (per-area override).
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'insurance_fee_percent'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN insurance_fee_percent FLOAT NOT NULL DEFAULT 2.0;
    END IF;

    -- Per-area platform / city fees.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'platform_fee'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN platform_fee FLOAT NOT NULL DEFAULT 0;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'city_fee'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN city_fee FLOAT NOT NULL DEFAULT 0.50;
    END IF;

    -- Per-area surge enable flag and currency.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'surge_enabled'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN surge_enabled BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'currency'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN currency TEXT NOT NULL DEFAULT 'CAD';
    END IF;
END $$;
