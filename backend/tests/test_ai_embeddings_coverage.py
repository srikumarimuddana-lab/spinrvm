"""Closes the remaining coverage gap on backend/ai/embeddings.py (A1c
Sub-tier C batch 11): the two provider-calling bodies, `_embed_openai` and
`_embed_gemini`. test_ai_embeddings.py exercises `embed_texts`'s soft-fail
contract thoroughly but always patches `_embed_openai` wholesale (never lets
the real body run), so the actual AsyncOpenAI / google.generativeai call
sites (lines 44-48, 52-62) were never covered.

Test-only: no application code in ai/embeddings.py is modified here.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.ai import embeddings as emb


def _ensure_real_openai_imported():
    """Force a fresh, real `openai` import before patching it.

    Defensive against a pre-existing test-suite hygiene issue (same class as
    A8): under the full suite, `sys.modules["openai"]` can end up replaced by
    an incomplete stand-in left behind by another test's imperfectly-scoped
    patch, so `patch("openai.AsyncOpenAI", ...)` fails with
    `AttributeError: <module 'openai'> does not have the attribute
    'AsyncOpenAI'` even though the real installed package has it. Dropping
    the (possibly-polluted) cache entry and re-importing guarantees `patch`
    resolves against the genuine module. Root cause not chased further here
    (test-only scope) — see this PR's Change Impact Log.
    """
    sys.modules.pop("openai", None)
    import openai  # noqa: F401


class TestEmbedOpenaiBody:
    @pytest.mark.anyio
    async def test_calls_async_openai_client_and_extracts_embeddings(self):
        item_a = MagicMock(embedding=[0.1, 0.2, 0.3])
        item_b = MagicMock(embedding=[0.4, 0.5, 0.6])
        resp = MagicMock(data=[item_a, item_b])

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.embeddings.create = AsyncMock(return_value=resp)

        _ensure_real_openai_imported()
        with patch("openai.AsyncOpenAI", return_value=client) as ctor:
            out = await emb._embed_openai(["hello", "world"], "sk-test", "text-embedding-3-small")

        assert out == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        ctor.assert_called_once_with(api_key="sk-test")
        client.embeddings.create.assert_awaited_once_with(model="text-embedding-3-small", input=["hello", "world"])

    @pytest.mark.anyio
    async def test_reachable_via_embed_texts_end_to_end(self):
        """Confirms the real (unpatched) _embed_openai body is what
        embed_texts actually invokes on the happy path — not just that the
        function works in isolation."""
        item = MagicMock(embedding=[1.0, 2.0])
        resp = MagicMock(data=[item])
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.embeddings.create = AsyncMock(return_value=resp)

        _ensure_real_openai_imported()
        with patch("openai.AsyncOpenAI", return_value=client):
            out = await emb.embed_texts(["hi"], {"ai_embedding_provider": "openai", "ai_api_key_openai": "sk-1"})

        assert out == [[1.0, 2.0]]


class TestEmbedGeminiBody:
    @pytest.mark.anyio
    async def test_calls_genai_embed_content_per_text(self):
        embed_calls: list[str] = []

        def _fake_embed_content(model, content):
            embed_calls.append(content)
            return {"embedding": [len(content) * 1.0, 0.0]}

        genai_mock = MagicMock()
        genai_mock.configure = MagicMock()
        genai_mock.embed_content = MagicMock(side_effect=_fake_embed_content)

        with patch.dict("sys.modules", {"google.generativeai": genai_mock}):
            out = await emb._embed_gemini(["ab", "cde"], "key-1", "models/text-embedding-004")

        assert out == [[2.0, 0.0], [3.0, 0.0]]
        assert embed_calls == ["ab", "cde"]
        genai_mock.configure.assert_called_once_with(api_key="key-1")

    @pytest.mark.anyio
    async def test_reachable_via_embed_texts_end_to_end(self):
        genai_mock = MagicMock()
        genai_mock.configure = MagicMock()
        genai_mock.embed_content = MagicMock(return_value={"embedding": [9.0]})

        with patch.dict("sys.modules", {"google.generativeai": genai_mock}):
            out = await emb.embed_texts(["only"], {"ai_embedding_provider": "gemini", "ai_api_key_gemini": "k"})

        assert out == [[9.0]]


class TestEmbedTextsTimeout:
    @pytest.mark.anyio
    async def test_timeout_returns_none(self):
        """A provider call that blows the EMBED_TIMEOUT_SECONDS budget must
        fall back to None (lexical matching), not hang the caller."""
        import asyncio

        async def _never_returns(*a, **k):
            await asyncio.sleep(10)

        with (
            patch.object(emb, "_embed_openai", _never_returns),
            patch.object(emb, "EMBED_TIMEOUT_SECONDS", 0.05),
        ):
            out = await emb.embed_texts(["slow"], {"ai_embedding_provider": "openai", "ai_api_key_openai": "k"})
        assert out is None
