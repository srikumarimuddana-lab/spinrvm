"""Text embeddings for semantic FAQ search.

Deliberately provider-agnostic and OPTIONAL: the chat provider may be Anthropic
(no embeddings endpoint), so embeddings use their own provider/credential picked
from settings (``ai_embedding_provider`` ∈ {openai, gemini}). Every entry point
fails SOFT — a disabled feature, missing key, or provider error returns None so
the caller falls back to lexical FAQ matching (backend/ai/tools_support.py).

Vectors are compared with plain cosine similarity in Python; at FAQ scale
(~hundreds of rows) that is trivial and needs no pgvector. FAQ vectors are
persisted as JSONB by the caller so they are embedded once, not per query.
"""

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Bounded so an embedding round-trip can never blow the 5s tool timeout — on
# timeout embed_texts returns None and the caller uses lexical matching.
EMBED_TIMEOUT_SECONDS = 3.5

_DEFAULT_MODELS = {
    "openai": "text-embedding-3-small",
    "gemini": "models/text-embedding-004",
}


def cosine(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 for empty/mismatched vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def _embed_openai(texts: List[str], api_key: str, model: str) -> List[List[float]]:
    from openai import AsyncOpenAI

    async with AsyncOpenAI(api_key=api_key) as client:
        resp = await client.embeddings.create(model=model, input=texts)
    return [list(item.embedding) for item in resp.data]


async def _embed_gemini(texts: List[str], api_key: str, model: str) -> List[List[float]]:
    import google.generativeai as genai

    def _run() -> List[List[float]]:
        genai.configure(api_key=api_key)
        out: List[List[float]] = []
        for text in texts:
            result = genai.embed_content(model=model, content=text)
            out.append(list(result["embedding"]))
        return out

    return await asyncio.to_thread(_run)


def embedding_model_for(settings: Dict[str, Any]) -> Optional[str]:
    """The concrete model id embeddings will use, or None when unconfigured.
    Callers tag persisted FAQ vectors with this so a model change re-embeds."""
    provider = (settings.get("ai_embedding_provider") or "").lower()
    if provider not in _DEFAULT_MODELS:
        return None
    return (settings.get("ai_embedding_model") or "").strip() or _DEFAULT_MODELS[provider]


async def embed_texts(texts: List[str], settings: Dict[str, Any]) -> Optional[List[List[float]]]:
    """Embed a batch of texts, or None if embeddings are unavailable.

    Never raises. Returns None when the feature is off, no provider/key is
    configured, or the provider call fails/times out — the caller MUST treat
    None as "fall back to lexical".
    """
    if not texts:
        return []
    provider = (settings.get("ai_embedding_provider") or "").lower()
    if provider not in _DEFAULT_MODELS:
        return None
    api_key = settings.get(f"ai_api_key_{provider}") or ""
    if not api_key:
        return None
    model = embedding_model_for(settings)

    try:
        runner = _embed_openai if provider == "openai" else _embed_gemini
        vectors = await asyncio.wait_for(runner(texts, api_key, model), timeout=EMBED_TIMEOUT_SECONDS)
    except Exception:
        logger.error("ai embeddings failed — falling back to lexical", exc_info=True, extra={"provider": provider})
        return None

    if not isinstance(vectors, list) or len(vectors) != len(texts):
        logger.error("ai embeddings returned %s vectors for %s inputs", len(vectors or []), len(texts))
        return None
    return vectors
