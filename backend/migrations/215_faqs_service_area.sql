-- 215_faqs_service_area.sql
--
-- Adds an optional service-area scope to faqs so FAQ content can be
-- location-specific (e.g. SGI / Saskatchewan Transportation rules for
-- Saskatchewan service areas, different regulatory content for another
-- province) while general FAQs stay global.
--
--   • service_area_id  TEXT  — FK to service_areas(id). NULL = global (shown in
--                             every area). Non-NULL = only shown to riders/drivers
--                             currently operating in that service area.
--
-- Consumed by:
--   • backend/ai/tools_support.py::search_faqs (AI assistant) — resolves the
--     user's current service area (device location, else the driver's area) and
--     returns global FAQs plus the ones tagged for that area.
--   • backend/routes/faqs.py (public list) — optional service_area_id / lat+lng
--     filter with the same global-or-matching rule.
--
-- ON DELETE SET NULL: removing a service area must not delete its FAQs — they
-- fall back to global rather than vanishing.
--
-- Forward-compatible: pure additive ADD COLUMN IF NOT EXISTS (nullable, no
-- default), metadata-only, no rewrite, safe against in-flight traffic. Existing
-- FAQs stay global (NULL), so behaviour is unchanged until rows are tagged.
--
-- Index: FAQ lookups filter on service_area_id; a partial index covers the
-- tagged (non-global) rows, which are the selective ones.
--
-- RLS: faqs is public reference content (no per-user data) — no new policy,
-- consistent with the existing faqs table and migration 48.
--
-- Rollback: (manual, not expected)
--   DROP INDEX IF EXISTS idx_faqs_service_area;
--   ALTER TABLE faqs DROP COLUMN IF EXISTS service_area_id;

ALTER TABLE faqs
    ADD COLUMN IF NOT EXISTS service_area_id TEXT REFERENCES service_areas(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_faqs_service_area
    ON faqs (service_area_id)
    WHERE service_area_id IS NOT NULL;
