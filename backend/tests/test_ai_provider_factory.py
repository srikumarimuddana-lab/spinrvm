"""Provider factory: settings-driven selection, key resolution, no fallback.

The runtime-swap contract: an admin changes ai_provider/ai_model/key in the
settings row and get_adapter() honours it on the next call. A missing key
or unknown provider raises AIConfigError — never a silent fallback to a
different provider.
"""

from unittest.mock import AsyncMock, patch

import pytest

import backend.ai.providers as providers
from backend.ai.providers import OPENROUTER_BASE_URL, get_adapter
from backend.ai.providers.anthropic_adapter import AnthropicAdapter
from backend.ai.providers.base import AIConfigError
from backend.ai.providers.gemini_adapter import GeminiAdapter
from backend.ai.providers.openai_adapter import OpenAIAdapter


def _settings(**overrides):
    base = {
        "ai_provider": "anthropic",
        "ai_model": "claude-haiku-4-5",
        "ai_api_key_anthropic": "sk-ant",
        "ai_api_key_openai": "",
        "ai_api_key_gemini": "",
        "ai_api_key_openrouter": "",
        "ai_max_output_tokens": 1024,
    }
    base.update(overrides)
    # get_adapter resolves the settings loader lazily via _get_app_settings_fn.
    return patch.object(providers, "_get_app_settings_fn", lambda: AsyncMock(return_value=base))


class TestSelection:
    @pytest.mark.anyio
    async def test_default_is_anthropic_haiku(self):
        with _settings(ai_provider="", ai_model=""):
            adapter = await get_adapter()
        assert isinstance(adapter, AnthropicAdapter)
        assert adapter.provider == "anthropic"
        assert adapter.model == "claude-haiku-4-5"
        assert adapter.api_key == "sk-ant"
        assert adapter.base_url is None

    @pytest.mark.anyio
    async def test_openrouter_is_openai_adapter_with_base_url(self):
        with _settings(
            ai_provider="openrouter",
            ai_model="anthropic/claude-haiku-4.5",
            ai_api_key_openrouter="sk-or",
        ):
            adapter = await get_adapter()
        assert isinstance(adapter, OpenAIAdapter)
        assert adapter.base_url == OPENROUTER_BASE_URL
        assert adapter.api_key == "sk-or"
        # labelled with the SELECTED provider, not the implementing class
        assert adapter.provider == "openrouter"

    @pytest.mark.anyio
    async def test_gemini_selection(self):
        with _settings(ai_provider="gemini", ai_model="gemini-2.5-flash", ai_api_key_gemini="g-key"):
            adapter = await get_adapter()
        assert isinstance(adapter, GeminiAdapter)
        assert adapter.model == "gemini-2.5-flash"

    @pytest.mark.anyio
    async def test_provider_string_normalized(self):
        with _settings(ai_provider="  OpenAI ", ai_api_key_openai="sk-oa"):
            adapter = await get_adapter()
        assert isinstance(adapter, OpenAIAdapter)
        assert adapter.base_url is None

    @pytest.mark.anyio
    async def test_max_tokens_flows_through(self):
        with _settings(ai_max_output_tokens=512):
            adapter = await get_adapter()
        assert adapter.max_tokens == 512


class TestFailures:
    @pytest.mark.anyio
    async def test_missing_key_raises_not_falls_back(self):
        with _settings(ai_api_key_anthropic=""), patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("AI_ANTHROPIC_API_KEY", None)
            with pytest.raises(AIConfigError) as exc:
                await get_adapter()
        assert exc.value.provider == "anthropic"

    @pytest.mark.anyio
    async def test_unknown_provider_raises(self):
        with _settings(ai_provider="grok"):
            with pytest.raises(AIConfigError) as exc:
                await get_adapter()
        assert "unsupported" in str(exc.value)

    @pytest.mark.anyio
    async def test_env_var_fallback(self):
        with _settings(ai_api_key_anthropic=""), patch.dict("os.environ", {"AI_ANTHROPIC_API_KEY": "sk-env"}):
            adapter = await get_adapter()
        assert adapter.api_key == "sk-env"
