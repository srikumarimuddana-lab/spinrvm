"""Embedding helpers: cosine math, model resolution, and the soft-fail
contract (disabled / unconfigured / provider error → None so the caller uses
lexical matching)."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.ai import embeddings as emb


class TestCosine:
    def test_identical_is_one(self):
        assert emb.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_is_zero(self):
        assert emb.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_is_negative_one(self):
        assert emb.cosine([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)

    def test_empty_or_mismatched_is_zero(self):
        assert emb.cosine([], [1.0]) == 0.0
        assert emb.cosine([1.0, 2.0], [1.0]) == 0.0
        assert emb.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestModelResolution:
    def test_default_model_per_provider(self):
        assert emb.embedding_model_for({"ai_embedding_provider": "openai"}) == "text-embedding-3-small"
        assert emb.embedding_model_for({"ai_embedding_provider": "gemini"}) == "models/text-embedding-004"

    def test_explicit_model_wins(self):
        got = emb.embedding_model_for({"ai_embedding_provider": "openai", "ai_embedding_model": "text-embedding-3-large"})
        assert got == "text-embedding-3-large"

    def test_unconfigured_is_none(self):
        assert emb.embedding_model_for({}) is None
        assert emb.embedding_model_for({"ai_embedding_provider": "anthropic"}) is None


class TestEmbedTexts:
    @pytest.mark.anyio
    async def test_empty_input_returns_empty(self):
        assert await emb.embed_texts([], {"ai_embedding_provider": "openai", "ai_api_key_openai": "k"}) == []

    @pytest.mark.anyio
    async def test_disabled_provider_returns_none(self):
        assert await emb.embed_texts(["hi"], {}) is None
        assert await emb.embed_texts(["hi"], {"ai_embedding_provider": "anthropic"}) is None

    @pytest.mark.anyio
    async def test_missing_key_returns_none(self):
        assert await emb.embed_texts(["hi"], {"ai_embedding_provider": "openai"}) is None

    @pytest.mark.anyio
    async def test_openai_path_returns_vectors(self):
        settings = {"ai_embedding_provider": "openai", "ai_api_key_openai": "k"}
        with patch.object(emb, "_embed_openai", AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])) as m:
            out = await emb.embed_texts(["a", "b"], settings)
        assert out == [[0.1, 0.2], [0.3, 0.4]]
        assert m.await_args.args[2] == "text-embedding-3-small"  # default model passed through

    @pytest.mark.anyio
    async def test_provider_error_returns_none(self):
        settings = {"ai_embedding_provider": "openai", "ai_api_key_openai": "k"}
        with patch.object(emb, "_embed_openai", AsyncMock(side_effect=RuntimeError("500"))):
            assert await emb.embed_texts(["a"], settings) is None

    @pytest.mark.anyio
    async def test_vector_count_mismatch_returns_none(self):
        settings = {"ai_embedding_provider": "openai", "ai_api_key_openai": "k"}
        with patch.object(emb, "_embed_openai", AsyncMock(return_value=[[0.1, 0.2]])):  # 1 vec for 2 inputs
            assert await emb.embed_texts(["a", "b"], settings) is None
