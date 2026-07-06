-- 211_settings_add_ai_semantic_columns.sql
--
-- Adds the settings columns for semantic FAQ search (backend/ai/embeddings.py +
-- backend/ai/tools_support.py::search_faqs), the counterparts to the fields
-- added in schemas.py AppSettings and the admin settings PATCH model.
--
-- Without these columns, admin_update_settings (routes/admin/settings.py) writes
-- the PATCH payload straight through, so the first Settings save that includes
-- any of these fields 503s with e.g.
--   PGRST204: Could not find the 'ai_embedding_provider' column of 'settings'
-- and the feature can never be enabled from the admin UI (same failure mode the
-- 145/208 headers describe for their own columns).
--
--   • ai_faq_semantic_enabled     BOOLEAN  — master on/off (default false)
--   • ai_embedding_provider       TEXT     — "" | openai | gemini
--   • ai_embedding_model          TEXT     — model id; blank → provider default
--   • ai_faq_semantic_min_score   NUMERIC  — cosine floor to count as a match
--                                            (default 0.30)
--
-- Forward-compatible: pure additive ADD COLUMN IF NOT EXISTS with defaults;
-- metadata-only, no rewrite, safe against in-flight traffic. settings is the
-- single-row service-role config table (no per-user data), so no RLS block —
-- consistent with migrations 145 and 208.
--
-- Rollback (manual, not expected):
--   ALTER TABLE settings
--     DROP COLUMN IF EXISTS ai_faq_semantic_enabled,
--     DROP COLUMN IF EXISTS ai_embedding_provider,
--     DROP COLUMN IF EXISTS ai_embedding_model,
--     DROP COLUMN IF EXISTS ai_faq_semantic_min_score;

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS ai_faq_semantic_enabled   BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS ai_embedding_provider     TEXT,
    ADD COLUMN IF NOT EXISTS ai_embedding_model        TEXT,
    ADD COLUMN IF NOT EXISTS ai_faq_semantic_min_score NUMERIC DEFAULT 0.30;
