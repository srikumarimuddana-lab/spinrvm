"""Curated provider → model catalog for the admin AI settings card.

Drives the two dropdowns (company, then model). Entries are SUGGESTIONS —
the admin UI also offers free-text entry, and an invalid id surfaces loudly
as a provider error on the next chat turn (never a silent fallback).

Anthropic ids are current API aliases. The OpenAI/Gemini/OpenRouter ids
reflect each vendor's lineup at the time of writing — verify against the
provider's model list when selecting, and prefer updating this table over
hand-typing ids. Keep entries cheap-to-capable ordered; the first entry is
the dropdown default for its provider.
"""

from typing import Any, Dict, List

PROVIDER_CATALOG: List[Dict[str, Any]] = [
    {
        "provider": "anthropic",
        "label": "Anthropic (Claude)",
        "key_field": "ai_api_key_anthropic",
        "models": [
            {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5 — fastest, cheapest (default)"},
            {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 — stronger reasoning"},
            {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 — most capable"},
        ],
    },
    {
        "provider": "openai",
        "label": "OpenAI (ChatGPT)",
        "key_field": "ai_api_key_openai",
        "models": [
            {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini — fast, low cost"},
            {"id": "gpt-5.4-nano", "label": "GPT-5.4 Nano — cheapest, weaker tool use"},
            {"id": "gpt-5.4", "label": "GPT-5.4 — most capable"},
        ],
    },
    {
        "provider": "gemini",
        "label": "Google (Gemini)",
        "key_field": "ai_api_key_gemini",
        "models": [
            {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash — low cost"},
            {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash — current flagship Flash"},
            {"id": "gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash-Lite — cheapest"},
        ],
    },
    {
        "provider": "openrouter",
        "label": "OpenRouter (any vendor, one key)",
        "key_field": "ai_api_key_openrouter",
        "models": [
            {"id": "anthropic/claude-haiku-4.5", "label": "Claude Haiku 4.5 via OpenRouter"},
            {"id": "openai/gpt-5.4-mini", "label": "GPT-5.4 Mini via OpenRouter"},
            {"id": "google/gemini-2.5-flash", "label": "Gemini 2.5 Flash via OpenRouter"},
        ],
    },
]


def get_catalog() -> Dict[str, Any]:
    return {"providers": PROVIDER_CATALOG}
