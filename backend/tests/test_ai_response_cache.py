"""FAQ response cache: key normalization, cacheability rules, and the
orchestrator wiring (hit skips the LLM; only self-contained impersonal turns
that used read-only tools are stored).

The orchestrator tests reuse the scripted FakeAdapter pattern from
test_ai_orchestrator and patch response_cache's get/store so no Redis is hit.
"""

from unittest.mock import AsyncMock, patch

import pytest

import backend.ai.orchestrator as orch
import backend.ai.response_cache as rc
from backend.ai.providers.base import StreamEvent, ToolCall

USER = {"id": "rider-1"}
CONV = {"id": "conv-1", "user_id": "rider-1", "audience": "rider", "title": None}

SETTINGS = {
    "ai_assistant_enabled": True,
    "ai_daily_message_cap": 50,
    "ai_history_max_messages": 12,
    "ai_max_tool_iterations": 3,
    "ai_faq_cache_enabled": True,
    "ai_faq_cache_ttl_seconds": 3600,
}


# ---------------------------------------------------------------- pure units


class TestKeyNormalization:
    def test_phrasing_variants_collide(self):
        assert rc.cache_key("rider", "How does surge work?") == rc.cache_key(
            "rider", "  how   does SURGE work "
        )

    def test_audience_separates_keys(self):
        assert rc.cache_key("rider", "how are payouts made") != rc.cache_key(
            "driver", "how are payouts made"
        )

    def test_distinct_questions_distinct_keys(self):
        assert rc.cache_key("rider", "what areas do you cover") != rc.cache_key(
            "rider", "how does surge work"
        )


class TestIsCacheable:
    BASE = dict(
        admin_actor_id=None,
        prior_turns=0,
        used_tool_names=["search_faqs"],
        had_client_action=False,
        text="Surge rises with demand.",
    )

    def test_faq_only_first_turn_is_cacheable(self):
        assert rc.is_cacheable(**self.BASE) is True

    def test_no_tool_turn_is_cacheable(self):
        assert rc.is_cacheable(**{**self.BASE, "used_tool_names": []}) is True

    def test_personal_tool_blocks_cache(self):
        assert rc.is_cacheable(**{**self.BASE, "used_tool_names": ["get_wallet_balance"]}) is False

    def test_mixed_tools_block_cache(self):
        assert (
            rc.is_cacheable(**{**self.BASE, "used_tool_names": ["search_faqs", "get_active_ride"]})
            is False
        )

    def test_follow_up_turn_not_cacheable(self):
        assert rc.is_cacheable(**{**self.BASE, "prior_turns": 1}) is False

    def test_admin_console_turn_not_cacheable(self):
        assert rc.is_cacheable(**{**self.BASE, "admin_actor_id": "admin-1"}) is False

    def test_client_action_blocks_cache(self):
        assert rc.is_cacheable(**{**self.BASE, "had_client_action": True}) is False

    def test_empty_text_not_cacheable(self):
        assert rc.is_cacheable(**{**self.BASE, "text": "   "}) is False


# ------------------------------------------------------- orchestrator wiring


class FakeAdapter:
    provider = "fake"
    model = "fake-model"

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0

    async def stream_turn(self, *, system, messages, tools):
        self.calls += 1
        for event in self.turns.pop(0):
            yield event


def _end(stop="end_turn", tool_calls=None):
    return StreamEvent(
        type="turn_end",
        tool_calls=tool_calls,
        stop_reason=stop,
        usage={"input_tokens": 100, "output_tokens": 10},
    )


def _text(t):
    return StreamEvent(type="text", text=t)


async def _run(adapter, *, settings=None, history=None, cached=None, admin_actor_id=None,
               message="how does surge work?", tool_result=({"ok": True}, True)):
    """Drive run_chat_turn with response_cache get/store patched. Returns
    (frames, get_mock, store_mock, adapter)."""
    get_mock = AsyncMock(return_value=cached)
    store_mock = AsyncMock()
    hist = history if history is not None else [{"role": "user", "content": message}]
    patches = [
        patch.object(orch, "get_app_settings", AsyncMock(return_value=dict(settings or SETTINGS))),
        patch.object(orch.conversations, "get_or_create_conversation", AsyncMock(return_value=dict(CONV))),
        patch.object(orch.conversations, "append_message",
                     AsyncMock(side_effect=lambda *a, **kw: {"id": f"msg-{a[1]}"})),
        patch.object(orch.conversations, "load_history", AsyncMock(return_value=hist)),
        patch.object(orch, "get_adapter", AsyncMock(return_value=adapter)),
        patch.object(orch, "execute_tool", AsyncMock(return_value=tool_result)),
        patch.object(orch, "tool_defs_for", lambda audience: []),
        patch.object(orch, "redis_incr", AsyncMock(return_value=1)),
        patch.object(orch, "redis_expire", AsyncMock()),
        patch.object(orch.response_cache, "get_cached", get_mock),
        patch.object(orch.response_cache, "store_cached", store_mock),
    ]
    frames = []
    for p in patches:
        p.start()
    try:
        async for frame in orch.run_chat_turn(
            user=USER, conversation_id="conv-1", user_message=message, admin_actor_id=admin_actor_id
        ):
            frames.append(frame)
    finally:
        for p in patches:
            p.stop()
    return frames, get_mock, store_mock, adapter


class TestCacheHit:
    @pytest.mark.anyio
    async def test_hit_skips_llm_and_persists_cached_answer(self):
        adapter = FakeAdapter([])  # would IndexError if the LLM were called
        frames, get_mock, store_mock, adapter = await _run(adapter, cached="Surge rises with demand.")
        names = [n for n, _ in frames]
        assert names == ["meta", "token", "done"]
        assert frames[1][1]["text"] == "Surge rises with demand."
        assert adapter.calls == 0  # LLM never invoked
        get_mock.assert_awaited_once()
        store_mock.assert_not_awaited()  # a hit does not re-store

    @pytest.mark.anyio
    async def test_hit_reports_zero_usage(self):
        frames, *_ = await _run(FakeAdapter([]), cached="hi")
        assert frames[-1][0] == "done"
        assert frames[-1][1]["usage"] == {"input_tokens": 0, "output_tokens": 0}


class TestCacheStore:
    @pytest.mark.anyio
    async def test_faq_only_turn_is_stored(self):
        call = ToolCall(id="t1", name="search_faqs", arguments={"query": "surge"})
        adapter = FakeAdapter([
            [_end(stop="tool_use", tool_calls=[call])],
            [_text("Surge rises with demand."), _end()],
        ])
        frames, get_mock, store_mock, _ = await _run(adapter, tool_result=({"results": []}, True))
        get_mock.assert_awaited_once()  # miss checked first
        store_mock.assert_awaited_once()
        args = store_mock.await_args.args
        assert args[0] == "rider"                       # audience
        assert args[2] == "Surge rises with demand."    # text
        assert args[3] == 3600                          # ttl

    @pytest.mark.anyio
    async def test_personal_tool_turn_not_stored(self):
        call = ToolCall(id="t1", name="get_wallet_balance", arguments={})
        adapter = FakeAdapter([
            [_end(stop="tool_use", tool_calls=[call])],
            [_text("Your balance is $42.50."), _end()],
        ])
        _, _, store_mock, _ = await _run(adapter, tool_result=({"balance": "42.50"}, True))
        store_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_plain_answer_no_tools_is_stored(self):
        adapter = FakeAdapter([[_text("We serve Saskatchewan."), _end()]])
        _, _, store_mock, _ = await _run(adapter)
        store_mock.assert_awaited_once()

    @pytest.mark.anyio
    async def test_generic_fallback_answer_not_stored(self):
        # model produced no text → the generic "couldn't finish" fallback is
        # served but must NOT be cached and replayed for the same question.
        adapter = FakeAdapter([[_end()]])
        frames, _, store_mock, _ = await _run(adapter)
        assert any(n == "token" and "rephrase" in p.get("text", "") for n, p in frames)
        store_mock.assert_not_awaited()


class TestCacheEligibility:
    @pytest.mark.anyio
    async def test_follow_up_turn_bypasses_cache_entirely(self):
        # two prior messages → not the first, context-free turn
        hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "how does surge work?"}]
        adapter = FakeAdapter([[_text("It rises with demand."), _end()]])
        _, get_mock, store_mock, _ = await _run(adapter, history=hist)
        get_mock.assert_not_awaited()
        store_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_admin_console_turn_bypasses_cache(self):
        adapter = FakeAdapter([[_text("It rises with demand."), _end()]])
        _, get_mock, store_mock, _ = await _run(adapter, admin_actor_id="admin-1")
        get_mock.assert_not_awaited()
        store_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_disabled_setting_bypasses_cache(self):
        adapter = FakeAdapter([[_text("It rises with demand."), _end()]])
        _, get_mock, store_mock, _ = await _run(
            adapter, settings=dict(SETTINGS, ai_faq_cache_enabled=False)
        )
        get_mock.assert_not_awaited()
        store_mock.assert_not_awaited()
