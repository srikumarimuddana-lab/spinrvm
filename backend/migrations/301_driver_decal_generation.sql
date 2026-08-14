-- Add decal generation tracking columns to drivers table.
-- decal_generated_at: when the decal document was generated (distinct from
--   decals_sent_at which tracks physical shipping).
-- decal_number: unique identifier printed on the decal (format: SPR-YYYY-NNNNN).

ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS decal_generated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS decal_number TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_drivers_decal_number
    ON drivers (decal_number)
    WHERE decal_number IS NOT NULL;
