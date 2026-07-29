-- 268_rides_legacy_import_metadata.sql
-- Purpose: Give rides the same import-provenance surface users (migration 256)
--          and drivers (migration 221) already have, so the legacy booking
--          import can record which batch wrote a historical ride and which
--          previous-app booking it came from.
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS public.idx_rides_legacy_old_booking_id;
--   ALTER TABLE public.rides
--     DROP COLUMN IF EXISTS legacy_import_metadata;
--
-- Notes:
-- - Additive, NOT NULL with a constant default: metadata-only change, instant
--   on PG11+, safe with production traffic in flight. Existing rides get '{}'
--   and every read path treats an empty blob as "not imported".
-- - The unique partial index is the idempotency guard for the importer: a
--   re-run of the same batch (or a resumed partial run) cannot insert a second
--   copy of a legacy booking. It also backs the importer's "already imported?"
--   prefetch, which filters on legacy_import_metadata->>'old_booking_id'.
--   CONCURRENTLY because rides is a hot table; the migration runner detects the
--   keyword and switches to autocommit for these statements.
-- - Keep raw PII (names, phones, addresses) out of this blob; it is for
--   provenance keys (batch, source, old IDs, timestamps) only. Note this column
--   is spread into API responses by the ride serializers, so it reaches
--   rider/driver clients — opaque identifiers only.

ALTER TABLE public.rides
  ADD COLUMN IF NOT EXISTS legacy_import_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.rides.legacy_import_metadata IS
  'Provenance for rides written by legacy-app imports ({batch, source, old_booking_id, old_booking_code, old_customer_id, old_driver_id, imported_at}). Admin/compliance use only; never raw PII.';

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_legacy_old_booking_id
  ON public.rides ((legacy_import_metadata->>'old_booking_id'))
  WHERE legacy_import_metadata->>'old_booking_id' IS NOT NULL;
