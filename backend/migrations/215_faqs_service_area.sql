-- 215_faqs_service_area.sql
--
-- Adds an optional, multi-valued service-area scope to faqs so a single FAQ can
-- be location-specific to one OR MORE service areas (e.g. one SGI /
-- Saskatchewan-rules FAQ tagged to every Saskatchewan service area) while
-- general FAQs stay global.
--
--   • service_area_ids  TEXT[]  — service_areas.id values this FAQ applies to.
--                                NULL or {} (empty) = global (shown in every
--                                area). Non-empty = shown only to riders/drivers
--                                currently operating in one of those areas.
--
-- (No FK on array elements — Postgres can't FK array members. A deleted service
-- area simply stops matching; a periodic/admin cleanup can prune stale ids.)
--
-- Consumed by:
--   • backend/ai/tools_support.py::search_faqs (AI assistant) — resolves the
--     user's current service area (device location, else the driver's area) and
--     returns global FAQs plus the ones whose service_area_ids include it.
--   • backend/routes/faqs.py (public list) — optional service_area_id / lat+lng
--     filter with the same global-or-membership rule.
--
-- Forward-compatible: pure additive ADD COLUMN IF NOT EXISTS (nullable, no
-- default), metadata-only, no rewrite, safe against in-flight traffic. Existing
-- FAQs stay global (NULL), so behaviour is unchanged until rows are tagged.
--
-- Index: GIN over the array supports containment/overlap lookups
-- (service_area_ids && ARRAY[...]) if the filter is ever pushed into SQL; the
-- AI/public paths currently filter in Python over the small FAQ set.
--
-- RLS: faqs is public reference content (no per-user data) — no new policy,
-- consistent with the existing faqs table and migration 48.
--
-- Rollback: (manual, not expected)
--   DROP INDEX IF EXISTS idx_faqs_service_area_ids;
--   ALTER TABLE faqs DROP COLUMN IF EXISTS service_area_ids;

ALTER TABLE faqs
    ADD COLUMN IF NOT EXISTS service_area_ids TEXT[];

CREATE INDEX IF NOT EXISTS idx_faqs_service_area_ids
    ON faqs USING GIN (service_area_ids)
    WHERE service_area_ids IS NOT NULL;
