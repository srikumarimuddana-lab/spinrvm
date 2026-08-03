"""Coverage gaps in ai/providers/__init__.py not exercised by
test_ai_provider_factory.py, which patches _get_app_settings_fn entirely and
never drives an invalid ai_max_output_tokens value or the dual-path adapter
module loader's fallback branch.
"""

import importlib
from unittest.mock import AsyncMock, patch

import pytest

import backend.ai.providers as providers

# The forced-ImportError fallback resolves the adapter via the *absolute*
# `ai.providers.<module>` path rather than `backend.ai.providers.<module>` —
# a genuine dual-import duplicate-module case (CLAUDE.md's dual-import
# convention), so the fallback-loaded class is a distinct object from the
# one imported here via the backend-prefixed path. Import the same absolute
# path the fallback itself uses for identity comparisons.
from ai.providers.anthropic_adapter import AnthropicAdapter as AbsoluteAnthropicAdapter
from ai.providers.openai_adapter import OpenAIAdapter as AbsoluteOpenAIAdapter
from backend.ai.providers import get_adapter


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
    return patch.object(providers, "_get_app_settings_fn", lambda: AsyncMock(return_value=base))


class TestMaxTokensFallback:
    @pytest.mark.anyio
    async def test_non_numeric_max_tokens_falls_back_to_1024(self):
        with _settings(ai_max_output_tokens="not-a-number"):
            adapter = await get_adapter()
        assert adapter.max_tokens == 1024

    @pytest.mark.anyio
    async def test_none_max_tokens_falls_back_to_1024_type_error(self):
        # int(None) raises TypeError, not ValueError — both are caught.
        with _settings(ai_max_output_tokens=None):
            adapter = await get_adapter()
        assert adapter.max_tokens == 1024


class TestGetAppSettingsFnReal:
    def test_returns_the_real_settings_loader_callable(self):
        """Unpatched — exercises the actual lazy-import body instead of the
        test double every other test in this module substitutes in."""
        from backend.settings_loader import get_app_settings

        fn = providers._get_app_settings_fn()
        assert fn is get_app_settings


class TestAdapterModuleLoaderFallback:
    def test_falls_back_to_absolute_import_when_relative_import_fails(self):
        """_load_adapter_class tries the relative `.module` import first; when
        that raises ImportError (e.g. top-level run mode) it must retry the
        absolute `ai.providers.module` path rather than propagating."""
        real_import_module = importlib.import_module

        def fake_import_module(name, package=None):
            if name.startswith("."):
                raise ImportError("simulated: no relative package context")
            return real_import_module(name, package)

        with patch("backend.ai.providers.importlib.import_module", side_effect=fake_import_module) as mock_import:
            cls = providers._load_adapter_class("anthropic_adapter", "AnthropicAdapter")

        assert cls is AbsoluteAnthropicAdapter
        # First call attempted the relative form and raised; second call used
        # the absolute fallback path.
        assert mock_import.call_count == 2
        first_call_args = mock_import.call_args_list[0].args
        assert first_call_args[0] == ".anthropic_adapter"
        second_call_args = mock_import.call_args_list[1].args
        assert second_call_args[0] == "ai.providers.anthropic_adapter"

    @pytest.mark.anyio
    async def test_get_adapter_still_works_through_the_fallback_path(self):
        """End-to-end: get_adapter() must still resolve a working adapter
        even when the relative-import leg of the loader fails."""
        real_import_module = importlib.import_module

        def fake_import_module(name, package=None):
            if name.startswith("."):
                raise ImportError("simulated")
            return real_import_module(name, package)

        with (
            _settings(ai_provider="openai", ai_api_key_openai="sk-oa"),
            patch("backend.ai.providers.importlib.import_module", side_effect=fake_import_module),
        ):
            adapter = await get_adapter()
        assert isinstance(adapter, AbsoluteOpenAIAdapter)
