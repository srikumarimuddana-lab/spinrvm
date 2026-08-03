"""Direct coverage of get_cached/store_cached's own try/except bodies.

test_ai_response_cache.py drives these indirectly through run_chat_turn with
orch.response_cache.get_cached/store_cached patched out entirely — so the
real Redis-wrapping implementation (including its "never raises" contract)
was never exercised. These tests call the module functions directly.
"""

from unittest.mock import AsyncMock, patch

import pytest

import backend.ai.response_cache as rc


class TestGetCached:
    @pytest.mark.anyio
    async def test_returns_the_redis_value_on_success(self):
        with patch.object(rc, "redis_get", AsyncMock(return_value="Surge rises with demand.")) as mock_get:
            result = await rc.get_cached("rider", "how does surge work?")
        assert result == "Surge rises with demand."
        mock_get.assert_awaited_once_with(rc.cache_key("rider", "how does surge work?"))

    @pytest.mark.anyio
    async def test_returns_none_when_redis_misses(self):
        with patch.object(rc, "redis_get", AsyncMock(return_value=None)):
            result = await rc.get_cached("rider", "how does surge work?")
        assert result is None

    @pytest.mark.anyio
    async def test_swallows_redis_error_and_returns_none(self):
        """A cache read fault must never break the chat turn — the caller
        falls through to a live LLM call."""
        with patch.object(rc, "redis_get", AsyncMock(side_effect=RuntimeError("redis down"))):
            result = await rc.get_cached("rider", "how does surge work?")
        assert result is None


class TestStoreCached:
    @pytest.mark.anyio
    async def test_persists_via_redis_set_with_ttl(self):
        with patch.object(rc, "redis_set", AsyncMock()) as mock_set:
            await rc.store_cached("rider", "how does surge work?", "Surge rises with demand.", 3600)
        mock_set.assert_awaited_once_with(
            rc.cache_key("rider", "how does surge work?"), "Surge rises with demand.", ttl=3600
        )

    @pytest.mark.anyio
    async def test_swallows_redis_error_without_raising(self):
        """A cache write fault must never break the chat turn either."""
        with patch.object(rc, "redis_set", AsyncMock(side_effect=RuntimeError("redis down"))):
            await rc.store_cached("rider", "how does surge work?", "text", 3600)  # must not raise
