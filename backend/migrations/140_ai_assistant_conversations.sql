-- 140_ai_assistant_conversations.sql
-- Rollback:
--   DROP TABLE IF EXISTS public.ai_messages;
--   DROP TABLE IF EXISTS public.ai_conversations;
--
-- Chat history for the rider/driver AI assistant (backend/ai/). PIPEDA-minimal
-- by design: only user-authored text (stored AFTER PII scrubbing) and
-- assistant replies are persisted. Tool-call arguments and tool results
-- (addresses, balances, GPS) live only in request memory and are never
-- written here — an assistant row keeps tool_names for transparency only.
-- Rows are purged after 90 days by purge_pii_retention (migration 141).

CREATE TABLE IF NOT EXISTS public.ai_conversations (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     text        NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    audience    text        NOT NULL DEFAULT 'rider' CHECK (audience IN ('rider', 'driver')),
    title       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ai_messages (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid        NOT NULL REFERENCES public.ai_conversations(id) ON DELETE CASCADE,
    role            text        NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text        NOT NULL,
    tool_names      text[],
    input_tokens    integer,
    output_tokens   integer,
    provider        text,
    model           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Conversation list endpoint: WHERE user_id = ? ORDER BY updated_at DESC.
CREATE INDEX IF NOT EXISTS ai_conversations_user_updated_idx
    ON public.ai_conversations (user_id, updated_at DESC);

-- Message history endpoint: WHERE conversation_id = ? ORDER BY created_at.
CREATE INDEX IF NOT EXISTS ai_messages_conversation_created_idx
    ON public.ai_messages (conversation_id, created_at);

-- Retention purge scans by age (migration 141).
CREATE INDEX IF NOT EXISTS ai_messages_created_idx
    ON public.ai_messages (created_at);

-- Clients reach this data only through the authenticated backend REST API
-- (/api/v1/ai/...); the frontend anon key must never touch these tables.
ALTER TABLE public.ai_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY ai_conversations_no_client_access
    ON public.ai_conversations
    FOR ALL
    TO authenticated
    USING (false)
    WITH CHECK (false);

CREATE POLICY ai_conversations_service_role
    ON public.ai_conversations
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY ai_messages_no_client_access
    ON public.ai_messages
    FOR ALL
    TO authenticated
    USING (false)
    WITH CHECK (false);

CREATE POLICY ai_messages_service_role
    ON public.ai_messages
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

COMMENT ON TABLE public.ai_conversations IS
    'AI assistant chat threads (rider/driver). Backend-only access; 90-day retention.';

COMMENT ON TABLE public.ai_messages IS
    'AI assistant messages. user rows are stored post-PII-scrub; tool payloads are never persisted.';

COMMENT ON COLUMN public.ai_messages.tool_names IS
    'Names of tools the assistant called for this reply — names only, never arguments or results.';

NOTIFY pgrst, 'reload schema';
