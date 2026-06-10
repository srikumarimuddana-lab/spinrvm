"""Anthropic adapter: canonical event stream + wire-format translation.

The ``anthropic`` SDK is not in the backend lockfile yet (see
requirements.in) — stream_turn imports it lazily, so these tests inject a
fake module into sys.modules. Pins:

- text deltas stream as canonical ``text`` events, then exactly one
  ``turn_end`` with stop reason + usage
- tool_use blocks become parsed ToolCall objects (arguments as dicts)
- canonical history (user / assistant+tool_calls / tool_result) translates
  to the Messages API wire shape, including is_error pass-through
- the system prompt carries a cache_control breakpoint and tools pass 1:1
"""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.ai.providers.anthropic_adapter import AnthropicAdapter, _to_wire
from backend.ai.providers.base import ToolCall


def _delta_event(text):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text))


class _FakeStream:
    def __init__(self, events, final):
        self._events = list(events)
        self._final = final

    def __aiter__(self):
        self._iter = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None

    async def get_final_message(self):
        return self._final


class _Ctx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


def _fake_anthropic(events, final, captured):
    """Build a fake `anthropic` module whose stream() records its kwargs."""

    class _Messages:
        def stream(self, **kwargs):
            captured.update(kwargs)
            return _Ctx(_FakeStream(events, final))

    class _AsyncAnthropic:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key
            self.messages = _Messages()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return SimpleNamespace(AsyncAnthropic=_AsyncAnthropic)


def _final(content=(), stop_reason="end_turn", usage=(120, 45)):
    return SimpleNamespace(
        content=list(content),
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]),
    )


async def _run(adapter, fake_module, **kwargs):
    events = []
    with patch.dict(sys.modules, {"anthropic": fake_module}):
        async for ev in adapter.stream_turn(
            system=kwargs.get("system", "You are Spinr's assistant."),
            messages=kwargs.get("messages", [{"role": "user", "content": "hi"}]),
            tools=kwargs.get("tools", []),
        ):
            events.append(ev)
    return events


@pytest.fixture
def adapter():
    return AnthropicAdapter(api_key="sk-test", model="claude-haiku-4-5", max_tokens=1024)


class TestStreamTurn:
    @pytest.mark.anyio
    async def test_text_turn(self, adapter):
        captured = {}
        fake = _fake_anthropic([_delta_event("Hel"), _delta_event("lo")], _final(), captured)
        events = await _run(adapter, fake)
        assert [e.type for e in events] == ["text", "text", "turn_end"]
        assert events[0].text + events[1].text == "Hello"
        end = events[-1]
        assert end.stop_reason == "end_turn"
        assert end.tool_calls is None
        assert end.usage == {"input_tokens": 120, "output_tokens": 45}

    @pytest.mark.anyio
    async def test_tool_use_turn(self, adapter):
        block = SimpleNamespace(type="tool_use", id="toolu_1", name="get_active_ride", input={})
        fake = _fake_anthropic([], _final(content=[block], stop_reason="tool_use"), {})
        events = await _run(adapter, fake)
        end = events[-1]
        assert end.stop_reason == "tool_use"
        assert end.tool_calls == [ToolCall(id="toolu_1", name="get_active_ride", arguments={})]

    @pytest.mark.anyio
    async def test_request_shape(self, adapter):
        captured = {}
        tools = [{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
        await _run(adapter, _fake_anthropic([], _final(), captured), tools=tools)
        assert captured["model"] == "claude-haiku-4-5"
        assert captured["max_tokens"] == 1024
        assert captured["api_key"] == "sk-test"
        # cache breakpoint on the system prompt
        assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
        # tools pass through 1:1
        assert captured["tools"] == tools

    @pytest.mark.anyio
    async def test_max_tokens_stop(self, adapter):
        fake = _fake_anthropic([_delta_event("partial")], _final(stop_reason="max_tokens"), {})
        events = await _run(adapter, fake)
        assert events[-1].stop_reason == "max_tokens"


class TestToWire:
    def test_user_and_plain_assistant(self):
        wire = _to_wire(
            [
                {"role": "user", "content": "where is my driver"},
                {"role": "assistant", "content": "On the way."},
            ]
        )
        assert wire == [
            {"role": "user", "content": "where is my driver"},
            {"role": "assistant", "content": "On the way."},
        ]

    def test_assistant_with_tool_calls(self):
        wire = _to_wire(
            [
                {
                    "role": "assistant",
                    "content": "Let me check.",
                    "tool_calls": [ToolCall(id="toolu_1", name="get_active_ride", arguments={"a": 1})],
                }
            ]
        )
        blocks = wire[0]["content"]
        assert blocks[0] == {"type": "text", "text": "Let me check."}
        assert blocks[1] == {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "get_active_ride",
            "input": {"a": 1},
        }

    def test_tool_result_with_error_flag(self):
        wire = _to_wire(
            [
                {
                    "role": "tool_result",
                    "tool_call_id": "toolu_1",
                    "tool_name": "get_active_ride",
                    "content": '{"error": "ride not found"}',
                    "is_error": True,
                }
            ]
        )
        assert wire[0]["role"] == "user"
        block = wire[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "toolu_1"
        assert block["is_error"] is True
