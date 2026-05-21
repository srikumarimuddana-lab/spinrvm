-- 91_driver_documents_expiry_date.sql
-- Add `expiry_date` to driver_documents so admins can record an expiry on
-- approval that survives outside the legacy per-doc-type columns on the
-- `drivers` row (license_expiry_date, insurance_expiry_date, …). Newer
-- document types like vehicle_registration have no legacy column, so the
-- expiry the admin set was silently dropped and the Documents tab kept
-- reading "Not set" forever — this column closes that gap.
--
-- Rollback (manual):
--   ALTER TABLE public.driver_documents DROP COLUMN IF EXISTS expiry_date;
--   DROP INDEX IF EXISTS idx_driver_documents_expiry_date;

ALTER TABLE public.driver_documents
    ADD COLUMN IF NOT EXISTS expiry_date TIMESTAMPTZ;

-- Partial index — most doc rows will have NULL expiry (rejected,
-- pending, or types that don't expire). Indexing only the non-null
-- subset keeps the index small while still serving the
-- expiring-docs query patterns.
CREATE INDEX IF NOT EXISTS idx_driver_documents_expiry_date
    ON public.driver_documents (expiry_date)
    WHERE expiry_date IS NOT NULL;

COMMENT ON COLUMN public.driver_documents.expiry_date IS
    'Expiry date set by admin on approval. Drives per-doc expiry cards in the admin slideout and the future doc-level go-online check. Newer doc types (e.g. vehicle_registration) have no legacy *_expiry_date column on drivers, so this is the canonical store for them.';
