-- 208_settings_add_ai_faq_cache.sql
--
-- Adds two settings columns for the AI assistant FAQ response cache
-- (backend/ai/response_cache.py, consumed by backend/ai/orchestrator.py).
--
-- Without these columns a settings PATCH that includes the new fields raises
--   PGRST204: Could not find the 'ai_faq_cache_enabled' column of 'settings'
-- and get_app_settings() falls back to the schema default (feature off).
--
--   • ai_faq_cache_enabled     BOOLEAN  — master on/off for the FAQ response
--                                         cache (default false; ships dark)
--   • ai_faq_cache_ttl_seconds INTEGER  — cached-answer TTL so FAQ edits
--                                         propagate (default 3600 = 1h)
--
-- Forward-compatible: pure additive ADD COLUMN IF NOT EXISTS with defaults;
-- safe against in-flight production traffic (no rewrite, no lock beyond the
-- brief catalog update). The settings table is a single-row config table.
--
-- Rollback: (manual, not expected)
--   ALTER TABLE settings
--     DROP COLUMN IF EXISTS ai_faq_cache_enabled,
--     DROP COLUMN IF EXISTS ai_faq_cache_ttl_seconds;
--
-- No RLS block: the settings table is service-role-only (admin-managed) and
-- carries no per-user data, consistent with the prior ai_* settings migrations.

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS ai_faq_cache_enabled     BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS ai_faq_cache_ttl_seconds INTEGER DEFAULT 3600;
