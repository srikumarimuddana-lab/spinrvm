"""AI assistant settings: AppSettings defaults + get_app_settings merge.

The AI assistant (backend/ai/) is configured entirely through the
app_settings row so provider/model/keys can be swapped at runtime from the
admin dashboard (60s TTL, no redeploy). These tests pin the contract the
feature relies on:

1. The feature ships dark — ai_assistant_enabled / ai_mcp_enabled default
   to False so a deploy without admin action exposes nothing.
2. Defaults select Anthropic Claude Haiku with bounded spend guardrails.
3. get_app_settings() merges the DB row over schema defaults, so a settings
   row written before the AI fields existed still yields every ai_* key.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.schemas import AppSettings


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """get_app_settings caches in-process for 60s — isolate each test."""
    import backend.settings_loader as settings_loader

    settings_loader._settings_cache = None
    yield
    settings_loader._settings_cache = None


class TestAppSettingsAIDefaults:
    def test_ai_assistant_ships_dark(self):
        s = AppSettings()
        assert s.ai_assistant_enabled is False
        assert s.ai_mcp_enabled is False

    def test_default_provider_is_anthropic_haiku(self):
        s = AppSettings()
        assert s.ai_provider == "anthropic"
        assert s.ai_model == "claude-haiku-4-5"

    def test_api_keys_default_empty(self):
        s = AppSettings()
        assert s.ai_api_key_anthropic == ""
        assert s.ai_api_key_openai == ""
        assert s.ai_api_key_gemini == ""
        assert s.ai_api_key_openrouter == ""

    def test_spend_guardrails_are_bounded(self):
        s = AppSettings()
        assert 0 < s.ai_max_output_tokens <= 4096
        assert 0 < s.ai_max_tool_iterations <= 10
        assert 0 < s.ai_daily_message_cap <= 500
        assert 0 < s.ai_history_max_messages <= 50

    def test_escalation_side_effect_off_by_default(self):
        assert AppSettings().ai_escalation_creates_ticket is False

    def test_disclaimer_mentions_911(self):
        # The assistant must never present itself as an emergency channel.
        assert "911" in AppSettings().ai_disclaimer


class TestGetAppSettingsMerge:
    @pytest.mark.anyio
    async def test_missing_row_returns_ai_defaults(self):
        import backend.settings_loader as settings_loader

        with patch.object(settings_loader.db_supabase, "get_rows", new=AsyncMock(return_value=[])):
            result = await settings_loader.get_app_settings()

        assert result["ai_assistant_enabled"] is False
        assert result["ai_provider"] == "anthropic"
        assert result["ai_model"] == "claude-haiku-4-5"

    @pytest.mark.anyio
    async def test_pre_ai_settings_row_still_yields_ai_keys(self):
        """A row written before migration of the AI fields lacks ai_* keys —
        the merge must fill them from schema defaults, not KeyError later."""
        import backend.settings_loader as settings_loader

        legacy_row = {"id": "app_settings", "google_maps_api_key": "gmaps-key"}
        with patch.object(
            settings_loader.db_supabase,
            "get_rows",
            new=AsyncMock(return_value=[legacy_row]),
        ):
            result = await settings_loader.get_app_settings()

        assert result["google_maps_api_key"] == "gmaps-key"
        assert result["ai_assistant_enabled"] is False
        assert result["ai_daily_message_cap"] == 50

    @pytest.mark.anyio
    async def test_row_overrides_ai_defaults(self):
        """Admin-set provider/model/key win over defaults — the runtime swap."""
        import backend.settings_loader as settings_loader

        row = {
            "id": "app_settings",
            "ai_assistant_enabled": True,
            "ai_provider": "openrouter",
            "ai_model": "anthropic/claude-haiku-4.5",
            "ai_api_key_openrouter": "sk-or-test",
        }
        with patch.object(settings_loader.db_supabase, "get_rows", new=AsyncMock(return_value=[row])):
            result = await settings_loader.get_app_settings()

        assert result["ai_assistant_enabled"] is True
        assert result["ai_provider"] == "openrouter"
        assert result["ai_model"] == "anthropic/claude-haiku-4.5"
        assert result["ai_api_key_openrouter"] == "sk-or-test"
        # Untouched AI keys keep their defaults.
        assert result["ai_max_tool_iterations"] == 6
