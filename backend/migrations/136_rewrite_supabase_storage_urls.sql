-- 136_rewrite_supabase_storage_urls.sql
--
-- Rollback (manual):
--   There is no automatic rollback because the old signed-URL tokens were
--   already project-specific and expired (1-hour TTL). To roll back, re-upload
--   the files or regenerate URLs from the old project — replace NEW_HOST with
--   OLD_HOST below and re-run, but note tokens will be stripped either way.
--
-- Purpose:
--   After migrating to a new Supabase project, all stored storage URLs still
--   reference the old project host. This migration rewrites every *_url column
--   that holds a Supabase Storage URL:
--
--     driver_documents.document_url   — signed URLs  → public-path URLs (token stripped)
--     rides.route_snapshot_url        — public URLs  → hostname replaced
--     vehicle_types.illustration_url  — public URLs  → hostname replaced
--     settings.ride_offer_sound_url   — public URLs  → hostname replaced
--
--   BEFORE RUNNING: replace the two placeholder values below with the actual
--   old and new Supabase project hostnames (just the subdomain part, e.g.
--   "dbbadhihiwztmqonbdke" and "soiwhd4fowvkfizrub").

DO $$
DECLARE
    old_host TEXT := 'dbbadhihiwztmqonbdke.supabase.co';
    new_host TEXT := '<NEW_SUPABASE_PROJECT_REF>.supabase.co';  -- ← fill in before running
BEGIN
    -- ── 1. driver_documents.document_url ─────────────────────────────────────
    -- Stored as signed URLs: .../object/sign/driver-documents/<key>?token=...
    -- The token is project-specific and already expired (1-hour TTL).
    -- Convert to public-path format so the backend proxy can resolve the key
    -- and the URL is also directly usable if the bucket is later made public.
    UPDATE driver_documents
    SET document_url = 'https://' || new_host
        || '/storage/v1/object/public/'
        || regexp_replace(
               document_url,
               '^.*/storage/v1/object/(?:sign|public)/(.+?)(\?.*)?$',
               '\1'
           )
    WHERE document_url LIKE '%' || old_host || '%';

    RAISE NOTICE 'driver_documents: % rows rewritten', (SELECT COUNT(*) FROM driver_documents WHERE document_url LIKE '%' || new_host || '%');

    -- ── 2. rides.route_snapshot_url ──────────────────────────────────────────
    -- Public URLs — simple hostname swap.
    UPDATE rides
    SET route_snapshot_url = REPLACE(route_snapshot_url, old_host, new_host)
    WHERE route_snapshot_url LIKE '%' || old_host || '%';

    RAISE NOTICE 'rides.route_snapshot_url: % rows rewritten', (SELECT COUNT(*) FROM rides WHERE route_snapshot_url LIKE '%' || new_host || '%');

    -- ── 3. vehicle_types.illustration_url ────────────────────────────────────
    UPDATE vehicle_types
    SET illustration_url = REPLACE(illustration_url, old_host, new_host)
    WHERE illustration_url LIKE '%' || old_host || '%';

    RAISE NOTICE 'vehicle_types.illustration_url: % rows rewritten', (SELECT COUNT(*) FROM vehicle_types WHERE illustration_url LIKE '%' || new_host || '%');

    -- ── 4. settings.ride_offer_sound_url ─────────────────────────────────────
    UPDATE settings
    SET ride_offer_sound_url = REPLACE(ride_offer_sound_url, old_host, new_host)
    WHERE ride_offer_sound_url LIKE '%' || old_host || '%';

    RAISE NOTICE 'settings.ride_offer_sound_url: % rows rewritten', (SELECT COUNT(*) FROM settings WHERE ride_offer_sound_url LIKE '%' || new_host || '%');

END $$;
