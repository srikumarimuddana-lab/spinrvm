-- 136_rewrite_supabase_storage_urls.sql
--
-- Rollback (manual): re-run with old_host / new_host swapped — but note that
-- signed-URL tokens are already stripped (converted to public-path format) so
-- they cannot be restored from a URL alone. Files themselves are untouched.
--
-- Purpose:
--   After migrating to a new Supabase project all stored storage URLs still
--   reference the old project host. This migration rewrites every *_url column
--   that holds a Supabase Storage URL.
--
--   The new host is read automatically from the `app.supabase_url` session
--   variable injected by migrate.py (SUPABASE_URL env var). No manual
--   placeholder substitution is required.
--
-- Tables rewritten:
--   driver_documents.document_url   — signed → public-path (token stripped)
--   rides.route_snapshot_url        — public URL, hostname swapped
--   vehicle_types.illustration_url  — public URL, hostname swapped
--   settings.ride_offer_sound_url   — public URL, hostname swapped

DO $$
DECLARE
    old_host TEXT := 'dbbadhihiwztmqonbdke.supabase.co';
    raw_url  TEXT := current_setting('app.supabase_url', true);  -- e.g. https://xxxx.supabase.co
    new_host TEXT;
    n        INT;
BEGIN
    -- Derive bare host from the full URL (strip scheme + trailing slashes)
    new_host := regexp_replace(raw_url, '^https?://', '');
    new_host := split_part(new_host, '/', 1);  -- drop any path component

    IF new_host IS NULL OR new_host = '' OR new_host = old_host THEN
        RAISE EXCEPTION
            'app.supabase_url is missing or still points to the old project (%). '
            'Set SUPABASE_URL to the new project URL before running this migration.',
            old_host;
    END IF;

    RAISE NOTICE 'Rewriting storage URLs: % → %', old_host, new_host;

    -- ── 1. driver_documents.document_url ─────────────────────────────────────
    -- Stored as signed URLs: .../object/sign/driver-documents/<key>?token=...
    -- Tokens are project-specific and have already expired (1-hour TTL).
    -- Convert to public-path format so the backend proxy can extract the key
    -- and the URL is usable once the bucket is made public if needed.
    UPDATE driver_documents
    SET document_url = 'https://' || new_host
        || '/storage/v1/object/public/'
        || regexp_replace(
               document_url,
               '^.*/storage/v1/object/(?:sign|public)/(.+?)(\?.*)?$',
               '\1'
           )
    WHERE document_url LIKE '%' || old_host || '%';
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE 'driver_documents.document_url: % rows rewritten', n;

    -- ── 2. rides.route_snapshot_url ──────────────────────────────────────────
    UPDATE rides
    SET route_snapshot_url = replace(route_snapshot_url, old_host, new_host)
    WHERE route_snapshot_url LIKE '%' || old_host || '%';
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE 'rides.route_snapshot_url: % rows rewritten', n;

    -- ── 3. vehicle_types.illustration_url ────────────────────────────────────
    UPDATE vehicle_types
    SET illustration_url = replace(illustration_url, old_host, new_host)
    WHERE illustration_url LIKE '%' || old_host || '%';
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE 'vehicle_types.illustration_url: % rows rewritten', n;

    -- ── 4. settings.ride_offer_sound_url ─────────────────────────────────────
    UPDATE settings
    SET ride_offer_sound_url = replace(ride_offer_sound_url, old_host, new_host)
    WHERE ride_offer_sound_url LIKE '%' || old_host || '%';
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE 'settings.ride_offer_sound_url: % rows rewritten', n;

END $$;
