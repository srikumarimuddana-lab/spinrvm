-- Migration: Add driver_notes table for staff notes on drivers

CREATE TABLE IF NOT EXISTS driver_notes (
    id          TEXT PRIMARY KEY,
    driver_id   TEXT NOT NULL,
    staff_id    TEXT,
    staff_name  TEXT,
    note        TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'general',  -- general, warning, document, status_change, complaint
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_driver_notes_driver ON driver_notes(driver_id);
CREATE INDEX IF NOT EXISTS idx_driver_notes_created ON driver_notes(created_at DESC);
