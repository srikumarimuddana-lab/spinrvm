-- Migration: Add driver_activity_log table for full audit timeline

CREATE TABLE IF NOT EXISTS driver_activity_log (
    id          TEXT PRIMARY KEY,
    driver_id   TEXT NOT NULL,
    event_type  TEXT NOT NULL,  -- registered, document_uploaded, document_approved, document_rejected,
                                -- verified, unverified, rejected, suspended, banned, unbanned,
                                -- reactivated, status_override, profile_updated, vehicle_updated,
                                -- went_online, went_offline, note_added, subscription_started,
                                -- subscription_cancelled, ride_completed
    title       TEXT NOT NULL,
    description TEXT,
    metadata    JSONB DEFAULT '{}'::JSONB,  -- extra context (old_status, new_status, reason, etc.)
    actor       TEXT,           -- 'system', 'driver', or admin staff name/id
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_driver_activity_driver ON driver_activity_log(driver_id);
CREATE INDEX IF NOT EXISTS idx_driver_activity_created ON driver_activity_log(created_at DESC);
