-- Batch dispatch: ride_offers table, driver acceptance_rate, service area config
-- Rollback:
--   DROP TABLE IF EXISTS ride_offers;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS acceptance_rate;
--   ALTER TABLE service_areas DROP COLUMN IF EXISTS max_simultaneous_offers;
--   ALTER TABLE service_areas DROP COLUMN IF EXISTS use_eta_ranking;

-- ── ride_offers ─────────────────────────────────────────────────────
-- Tracks per-driver offers for batch dispatch. Ride stays in
-- 'searching' while multiple offers are pending; first accept wins.
CREATE TABLE IF NOT EXISTS ride_offers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id         TEXT NOT NULL REFERENCES rides(id) ON DELETE CASCADE,
    driver_id       TEXT NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending',
    eta_seconds     INT,
    offered_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    responded_at    TIMESTAMPTZ,
    CONSTRAINT ride_offers_status_check CHECK (status IN ('pending', 'accepted', 'declined', 'expired')),
    CONSTRAINT ride_offers_ride_driver_uq UNIQUE (ride_id, driver_id)
);

CREATE INDEX IF NOT EXISTS idx_ride_offers_ride_id
    ON ride_offers(ride_id);

CREATE INDEX IF NOT EXISTS idx_ride_offers_driver_status
    ON ride_offers(driver_id, status);

CREATE INDEX IF NOT EXISTS idx_ride_offers_pending
    ON ride_offers(ride_id) WHERE status = 'pending';

-- RLS: service role bypasses; drivers can read their own offers.
ALTER TABLE ride_offers ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'ride_offers' AND policyname = 'ride_offers_driver_select'
    ) THEN
        EXECUTE 'CREATE POLICY ride_offers_driver_select ON ride_offers
            FOR SELECT USING (driver_id IN (
                SELECT id FROM drivers WHERE user_id = auth.uid()::text
            ))';
    END IF;
END $$;

-- ── drivers.acceptance_rate ─────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'drivers' AND column_name = 'acceptance_rate'
    ) THEN
        ALTER TABLE drivers ADD COLUMN acceptance_rate FLOAT DEFAULT 1.0;
    END IF;
END $$;

-- ── service_areas dispatch config ───────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'max_simultaneous_offers'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN max_simultaneous_offers INT DEFAULT 3;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'use_eta_ranking'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN use_eta_ranking BOOLEAN DEFAULT true;
    END IF;
END $$;
