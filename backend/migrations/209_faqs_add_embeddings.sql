-- 209_faqs_add_embeddings.sql
--
-- Adds embedding storage to faqs for semantic FAQ search
-- (backend/ai/embeddings.py + backend/ai/tools_support.py::search_faqs).
--
-- Vectors are stored as a JSONB array of floats (no pgvector dependency);
-- cosine similarity is computed in Python at FAQ scale (~hundreds of rows).
-- Each row records the embedding_model it was produced with so a model change
-- (settings.ai_embedding_model) transparently re-embeds on next search.
--
--   • embedding        JSONB  — array of floats, NULL until first embedded
--   • embedding_model  TEXT   — model id that produced `embedding`, NULL if none
--
-- Forward-compatible: pure additive ADD COLUMN IF NOT EXISTS, no default
-- backfill, safe against in-flight traffic. Semantic search is gated behind
-- settings.ai_faq_semantic_enabled and falls back to lexical matching when a
-- row has no embedding, so this migration is inert until the feature is turned
-- on and rows are embedded lazily.
--
-- RLS: faqs is public reference content (already readable; no per-user data),
-- so no new policy is required — consistent with the existing faqs table.
--
-- Rollback: (manual, not expected)
--   ALTER TABLE faqs
--     DROP COLUMN IF EXISTS embedding,
--     DROP COLUMN IF EXISTS embedding_model;

ALTER TABLE faqs
    ADD COLUMN IF NOT EXISTS embedding       JSONB,
    ADD COLUMN IF NOT EXISTS embedding_model TEXT;
