"""Provider adapters for the AI assistant + runtime factory.

Each adapter normalizes one vendor SDK to the canonical types in base.py;
the provider-agnostic tool loop lives in backend/ai/orchestrator.py.

get_adapter() reads the app_settings row per request (60s TTL cache in
settings_loader), so an admin can swap provider/model/key from the
dashboard and the change takes effect within a minute — no redeploy.
A missing key raises AIConfigError, which the orchestrator surfaces loudly
(Sentry + SSE error frame). There is no silent fallback provider.
"""

import importlib
import os

try:
    from .base import AIConfigError, BaseLLMAdapter
except ImportError:
    from ai.providers.base import AIConfigError, BaseLLMAdapter


def _get_app_settings_fn():
    """Resolved lazily so importing an adapter module never drags the
    settings/db chain (keeps adapter unit tests dependency-free)."""
    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    return get_app_settings


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# provider → (adapter module, class name, base_url override)
_PROVIDERS = {
    "anthropic": ("anthropic_adapter", "AnthropicAdapter", None),
    "openai": ("openai_adapter", "OpenAIAdapter", None),
    "openrouter": ("openai_adapter", "OpenAIAdapter", OPENROUTER_BASE_URL),
    "gemini": ("gemini_adapter", "GeminiAdapter", None),
}

SUPPORTED_PROVIDERS = tuple(_PROVIDERS)


def _load_adapter_class(module_name: str, class_name: str):
    """Lazy dual-path import so a vendor SDK missing from the environment
    only breaks the provider that needs it, at the moment it is selected."""
    try:
        module = importlib.import_module(f".{module_name}", __package__)
    except ImportError:
        module = importlib.import_module(f"ai.providers.{module_name}")
    return getattr(module, class_name)


async def get_adapter() -> BaseLLMAdapter:
    settings = await _get_app_settings_fn()()
    provider = (settings.get("ai_provider") or "anthropic").strip().lower()
    if provider not in _PROVIDERS:
        raise AIConfigError(provider, f"unsupported provider (supported: {', '.join(SUPPORTED_PROVIDERS)})")

    model = (settings.get("ai_model") or "claude-haiku-4-5").strip()
    api_key = settings.get(f"ai_api_key_{provider}") or os.environ.get(f"AI_{provider.upper()}_API_KEY") or ""
    if not api_key:
        raise AIConfigError(provider, "no API key configured (admin → Settings → AI Assistant)")

    try:
        max_tokens = int(settings.get("ai_max_output_tokens") or 1024)
    except (TypeError, ValueError):
        max_tokens = 1024

    module_name, class_name, base_url = _PROVIDERS[provider]
    adapter_cls = _load_adapter_class(module_name, class_name)
    adapter = adapter_cls(api_key=api_key, model=model, max_tokens=max_tokens, base_url=base_url)
    # Label the instance with the *selected* provider so metrics and
    # persistence distinguish openrouter from first-party openai.
    adapter.provider = provider
    return adapter
