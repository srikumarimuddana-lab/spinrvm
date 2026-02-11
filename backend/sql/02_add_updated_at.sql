
-- Fix: Ensure 'updated_at' column exists on all tables
-- This script is safe to run multiple times.

DO $$
BEGIN
    -- Drivers
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='drivers' AND column_name='updated_at') THEN
        ALTER TABLE drivers ADD COLUMN updated_at timestamptz DEFAULT now();
    END IF;

    -- Users
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='updated_at') THEN
        ALTER TABLE users ADD COLUMN updated_at timestamptz DEFAULT now();
    END IF;

    -- Rides
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='rides' AND column_name='updated_at') THEN
        ALTER TABLE rides ADD COLUMN updated_at timestamptz DEFAULT now();
    END IF;

    -- Settings
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='settings' AND column_name='updated_at') THEN
        ALTER TABLE settings ADD COLUMN updated_at timestamptz DEFAULT now();
    END IF;
END $$;
