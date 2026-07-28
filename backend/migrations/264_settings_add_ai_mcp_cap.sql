-- 264_settings_add_ai_mcp_cap.sql
--
-- Adds the settings column for the /mcp per-user daily tool-call cap
-- (backend/ai/mcp_server.py::_over_mcp_daily_cap), the counterpart to the
-- field added in schemas.py AppSettings and the admin settings PATCH model.
--
-- Without this column, admin_update_settings (routes/admin/settings.py)
-- writes the PATCH payload straight through, so the first Settings save that
-- includes ai_mcp_daily_tool_cap 503s with
--   PGRST204: Could not find the 'ai_mcp_daily_tool_cap' column of 'settings'
-- and the cap can never be tuned from the admin UI (same failure mode the
-- 145/208/211 headers describe for their own columns).
--
--   • ai_mcp_daily_tool_cap  INTEGER — per-user/day tool calls on /mcp;
--                            0 (default) → fall back to ai_daily_message_cap
--
-- Forward-compatible: pure additive ADD COLUMN IF NOT EXISTS with a default;
-- metadata-only, no rewrite, safe against in-flight traffic. settings is the
-- single-row service-role config table (no per-user data), so no RLS block —
-- consistent with migrations 145, 208 and 211.
--
-- Rollback: (manual, not expected)
--   ALTER TABLE settings DROP COLUMN IF EXISTS ai_mcp_daily_tool_cap;

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS ai_mcp_daily_tool_cap INTEGER DEFAULT 0;

COMMENT ON COLUMN public.settings.ai_mcp_daily_tool_cap IS
    'Per-user daily tool-call cap on the /mcp surface. 0 = unset: fall back to ai_daily_message_cap.';
