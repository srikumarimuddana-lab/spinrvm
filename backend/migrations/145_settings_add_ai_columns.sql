-- 145_settings_add_ai_columns.sql
--
-- Add the AI-assistant configuration columns to the settings table.
--
-- The AI assistant feature (backend/ai/*, routes/ai.py, routes/admin/ai_console.py)
-- reads its provider, model, per-provider API keys, and limits from the single
-- settings row (id = 'app_settings'), set via the admin dashboard Settings page.
-- The feature code shipped WITHOUT this migration, so every admin Settings save
-- and every chat turn failed with e.g.:
--   PGRST204: Could not find the 'ai_api_key_anthropic' column of 'settings'
--   PGRST204: Could not find the 'ai_disclaimer' column of 'settings'
-- -> PUT /api/admin/settings 503, POST /admin/ai/chat 500/503.
--
-- The column set below mirrors EXACTLY the SettingsUpdateRequest model in
-- routes/admin/settings.py (the authoritative write set), so the admin Settings
-- save has no remaining missing columns. All nullable / safe defaults that match
-- the orchestrator's code-side fallbacks (`settings.get(...) or <default>`), so
-- the migration is forward-compatible with running traffic.
--
-- Columns landed:
--   • ai_assistant_enabled         BOOLEAN  — master on/off (default false)
--   • ai_mcp_enabled               BOOLEAN  — enable MCP tools (default false)
--   • ai_provider                  TEXT     — anthropic | openai | gemini | openrouter
--   • ai_model                     TEXT     — model id within the chosen provider
--   • ai_api_key_anthropic         TEXT     — per-provider credential (masked on GET
--   • ai_api_key_openai            TEXT       via _mask_credentials in
--   • ai_api_key_gemini            TEXT       routes/admin/settings.py; never returned
--   • ai_api_key_openrouter        TEXT       in plaintext on a settings GET)
--   • ai_max_output_tokens         INTEGER  — per-turn output cap (default 1024)
--   • ai_max_tool_iterations       INTEGER  — tool-loop ceiling per turn (default 6)
--   • ai_daily_message_cap         INTEGER  — per-user/day message cap (default 50)
--   • ai_history_max_messages      INTEGER  — context window of prior turns (default 12)
--   • ai_escalation_creates_ticket BOOLEAN  — auto-open a support ticket (default false)
--   • ai_disclaimer                TEXT     — disclaimer shown to users via /ai/config
--
-- No RLS change: the settings table is service-role-only (backend reads/writes
-- via the service key); the anon/frontend key never touches it directly. Adding
-- columns inherits the table's existing policies.
--
-- After applying, PostgREST must reload its schema cache (Supabase auto-reloads
-- on DDL via the pgrst NOTIFY; if PGRST204 persists, run
-- `NOTIFY pgrst, 'reload schema';` or restart the PostgREST service).
--
-- Rollback:
--   ALTER TABLE settings
--     DROP COLUMN IF EXISTS ai_assistant_enabled,
--     DROP COLUMN IF EXISTS ai_mcp_enabled,
--     DROP COLUMN IF EXISTS ai_provider,
--     DROP COLUMN IF EXISTS ai_model,
--     DROP COLUMN IF EXISTS ai_api_key_anthropic,
--     DROP COLUMN IF EXISTS ai_api_key_openai,
--     DROP COLUMN IF EXISTS ai_api_key_gemini,
--     DROP COLUMN IF EXISTS ai_api_key_openrouter,
--     DROP COLUMN IF EXISTS ai_max_output_tokens,
--     DROP COLUMN IF EXISTS ai_max_tool_iterations,
--     DROP COLUMN IF EXISTS ai_daily_message_cap,
--     DROP COLUMN IF EXISTS ai_history_max_messages,
--     DROP COLUMN IF EXISTS ai_escalation_creates_ticket,
--     DROP COLUMN IF EXISTS ai_disclaimer;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS ai_assistant_enabled         BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS ai_mcp_enabled               BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS ai_provider                  TEXT,
    ADD COLUMN IF NOT EXISTS ai_model                     TEXT,
    ADD COLUMN IF NOT EXISTS ai_api_key_anthropic         TEXT,
    ADD COLUMN IF NOT EXISTS ai_api_key_openai            TEXT,
    ADD COLUMN IF NOT EXISTS ai_api_key_gemini            TEXT,
    ADD COLUMN IF NOT EXISTS ai_api_key_openrouter        TEXT,
    ADD COLUMN IF NOT EXISTS ai_max_output_tokens         INTEGER DEFAULT 1024,
    ADD COLUMN IF NOT EXISTS ai_max_tool_iterations       INTEGER DEFAULT 6,
    ADD COLUMN IF NOT EXISTS ai_daily_message_cap         INTEGER DEFAULT 50,
    ADD COLUMN IF NOT EXISTS ai_history_max_messages      INTEGER DEFAULT 12,
    ADD COLUMN IF NOT EXISTS ai_escalation_creates_ticket BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS ai_disclaimer                TEXT;

COMMENT ON COLUMN public.settings.ai_assistant_enabled IS
    'Master on/off for the in-app AI assistant. Off until enabled in admin Settings (orchestrator returns ai_disabled/503 when false).';
COMMENT ON COLUMN public.settings.ai_mcp_enabled IS
    'Enable MCP tool access for the assistant (checked per request; default false).';
COMMENT ON COLUMN public.settings.ai_provider IS
    'Active LLM provider: anthropic | openai | gemini | openrouter. Must have a matching ai_api_key_<provider> set or the next chat turn returns ai_misconfigured.';
COMMENT ON COLUMN public.settings.ai_model IS
    'Model id within the chosen provider (see backend/ai/catalog.py PROVIDER_CATALOG for valid ids).';
COMMENT ON COLUMN public.settings.ai_api_key_anthropic IS
    'Anthropic API credential. Masked on GET via _mask_credentials in routes/admin/settings.py; never returned in plaintext.';
COMMENT ON COLUMN public.settings.ai_api_key_openai IS
    'OpenAI API credential. Masked on GET via _mask_credentials in routes/admin/settings.py; never returned in plaintext.';
COMMENT ON COLUMN public.settings.ai_api_key_gemini IS
    'Google Gemini API credential. Masked on GET via _mask_credentials in routes/admin/settings.py; never returned in plaintext.';
COMMENT ON COLUMN public.settings.ai_api_key_openrouter IS
    'OpenRouter API credential. Masked on GET via _mask_credentials in routes/admin/settings.py; never returned in plaintext.';
COMMENT ON COLUMN public.settings.ai_max_output_tokens IS
    'Max output tokens per assistant turn (orchestrator default 1024 when null).';
COMMENT ON COLUMN public.settings.ai_max_tool_iterations IS
    'Max tool-call iterations per assistant turn (orchestrator default 6 when null).';
COMMENT ON COLUMN public.settings.ai_daily_message_cap IS
    'Per-user daily AI message cap (orchestrator default 50 when null).';
COMMENT ON COLUMN public.settings.ai_history_max_messages IS
    'Max prior conversation turns loaded as context (orchestrator default 12 when null).';
COMMENT ON COLUMN public.settings.ai_escalation_creates_ticket IS
    'When true, an assistant escalation auto-opens a support ticket (default false).';
COMMENT ON COLUMN public.settings.ai_disclaimer IS
    'Disclaimer text surfaced to users via GET /ai/config (gates UI entry points).';
