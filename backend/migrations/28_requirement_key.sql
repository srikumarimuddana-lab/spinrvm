-- Migration 28: Add requirement_key to driver_documents
--
-- requirement_id is a UUID column — service-area doc keys like
-- "vehicle_registration" can't be stored there. requirement_key
-- holds the raw string key so admin can match docs to service-area
-- requirements even when requirement_id is NULL.

ALTER TABLE driver_documents
  ADD COLUMN IF NOT EXISTS requirement_key TEXT;

-- Index for admin lookups by key
CREATE INDEX IF NOT EXISTS idx_driver_documents_requirement_key
  ON driver_documents (requirement_key);
