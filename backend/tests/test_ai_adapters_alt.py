"""OpenAI (+OpenRouter) and Gemini adapters.

Both SDKs are stubbed via sys.modules (lazy imports in the adapters). Pins:
- OpenAI: tool-def translation, fragmented tool_call accumulation across
  chunks, finish_reason mapping, usage from the include_usage chunk, and
  base_url passthrough (the OpenRouter mode)
- Gemini: unsupported JSON-Schema keyword stripping, synthesized tool-call
  ids, tool_result → function_response wire shape
"""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.ai.providers.base import ToolCall
from backend.ai.providers.gemini_adapter import GeminiAdapter, _clean_schema
from backend.ai.providers.gemini_adapter import _to_wire as gemini_to_wire
from backend.ai.providers.openai_adapter import OpenAIAdapter
from backend.ai.providers.openai_adapter import _to_wire as openai_to_wire

TOOLS = [
    {
        "name": "get_active_ride",
        "description": "d",
        "input_schema": {
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 100}},
            "required": [],
            "additionalProperties": False,
        },
    }
]


# ── OpenAI fakes ─────────────────────────────────────────────────────────


def _oa_chunk(content=None, tool_frags=None, finish=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_frags)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice] if (content or tool_frags or finish) else [], usage=usage)


def _oa_frag(index=0, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        self._it = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


def _fake_openai(chunks, captured):
    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _AsyncIter(chunks)

    class _AsyncOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=_Completions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return SimpleNamespace(AsyncOpenAI=_AsyncOpenAI)


async def _run_openai(adapter, chunks, captured):
    events = []
    with patch.dict(sys.modules, {"openai": _fake_openai(chunks, captured)}):
        async for ev in adapter.stream_turn(system="sys", messages=[{"role": "user", "content": "hi"}], tools=TOOLS):
            events.append(ev)
    return events


class TestOpenAIAdapter:
    @pytest.mark.anyio
    async def test_fragmented_tool_call_accumulation(self):
        chunks = [
            _oa_chunk(tool_frags=[_oa_frag(0, id="call_1", name="get_active", arguments="")]),
            _oa_chunk(tool_frags=[_oa_frag(0, name="_ride", arguments='{"li')]),
            _oa_chunk(tool_frags=[_oa_frag(0, arguments='mit": 5}')], finish="tool_calls"),
            _oa_chunk(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20)),
        ]
        adapter = OpenAIAdapter(api_key="k", model="gpt-test", max_tokens=512)
        events = await _run_openai(adapter, chunks, {})
        end = events[-1]
        assert end.stop_reason == "tool_use"
        assert end.tool_calls == [ToolCall(id="call_1", name="get_active_ride", arguments={"limit": 5})]
        assert end.usage == {"input_tokens": 100, "output_tokens": 20}

    @pytest.mark.anyio
    async def test_text_turn_and_finish_mapping(self):
        chunks = [_oa_chunk(content="Hel"), _oa_chunk(content="lo", finish="stop")]
        adapter = OpenAIAdapter(api_key="k", model="gpt-test", max_tokens=512)
        events = await _run_openai(adapter, chunks, {})
        assert [e.type for e in events] == ["text", "text", "turn_end"]
        assert events[-1].stop_reason == "end_turn"

    @pytest.mark.anyio
    async def test_length_maps_to_max_tokens(self):
        chunks = [_oa_chunk(content="x", finish="length")]
        adapter = OpenAIAdapter(api_key="k", model="gpt-test", max_tokens=512)
        events = await _run_openai(adapter, chunks, {})
        assert events[-1].stop_reason == "max_tokens"

    @pytest.mark.anyio
    async def test_openrouter_base_url_and_tool_translation(self):
        captured = {}
        adapter = OpenAIAdapter(
            api_key="sk-or",
            model="anthropic/claude-haiku-4.5",
            max_tokens=512,
            base_url="https://openrouter.ai/api/v1",
        )
        await _run_openai(adapter, [_oa_chunk(finish="stop")], captured)
        assert captured["client_kwargs"] == {"api_key": "sk-or", "base_url": "https://openrouter.ai/api/v1"}
        fn = captured["tools"][0]
        assert fn["type"] == "function"
        assert fn["function"]["name"] == "get_active_ride"
        assert fn["function"]["parameters"] == TOOLS[0]["input_schema"]
        assert captured["stream_options"] == {"include_usage": True}

    def test_wire_shapes(self):
        wire = openai_to_wire(
            "sys",
            [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [ToolCall(id="call_1", name="t", arguments={"a": 1})],
                },
                {"role": "tool_result", "tool_call_id": "call_1", "content": '{"ok": true}'},
            ],
        )
        assert wire[0] == {"role": "system", "content": "sys"}
        assert wire[2]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'
        assert wire[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'}


# ── Gemini fakes ─────────────────────────────────────────────────────────


def _gm_part(text=None, fc=None):
    return SimpleNamespace(text=text, function_call=fc)


def _gm_chunk(parts, finish_reason="", usage=None):
    candidate = SimpleNamespace(content=SimpleNamespace(parts=parts), finish_reason=finish_reason)
    return SimpleNamespace(candidates=[candidate], usage_metadata=usage)


def _fake_genai(chunks, captured):
    class _Model:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

        async def generate_content_async(self, contents, stream=False):
            captured["contents"] = contents
            return _AsyncIter(chunks)

    return SimpleNamespace(configure=lambda **kw: captured.update(kw), GenerativeModel=_Model)


async def _run_gemini(adapter, chunks, captured):
    fake = _fake_genai(chunks, captured)
    events = []
    with patch.dict(
        sys.modules,
        {"google": SimpleNamespace(generativeai=fake), "google.generativeai": fake},
    ):
        async for ev in adapter.stream_turn(system="sys", messages=[{"role": "user", "content": "hi"}], tools=TOOLS):
            events.append(ev)
    return events


class TestGeminiAdapter:
    @pytest.mark.anyio
    async def test_text_then_function_call(self):
        fc = SimpleNamespace(name="get_active_ride", args={"limit": 2})
        chunks = [
            _gm_chunk([_gm_part(text="Checking…")]),
            _gm_chunk(
                [_gm_part(fc=fc)],
                usage=SimpleNamespace(prompt_token_count=80, candidates_token_count=10),
            ),
        ]
        adapter = GeminiAdapter(api_key="k", model="gemini-test", max_tokens=512)
        captured = {}
        events = await _run_gemini(adapter, chunks, captured)
        assert events[0].type == "text"
        end = events[-1]
        assert end.stop_reason == "tool_use"
        # synthesized id — Gemini has none
        assert end.tool_calls == [ToolCall(id="get_active_ride-0", name="get_active_ride", arguments={"limit": 2})]
        assert end.usage == {"input_tokens": 80, "output_tokens": 10}
        assert captured["api_key"] == "k"

    @pytest.mark.anyio
    async def test_schema_cleaning_in_declared_tools(self):
        captured = {}
        adapter = GeminiAdapter(api_key="k", model="gemini-test", max_tokens=512)
        await _run_gemini(adapter, [_gm_chunk([_gm_part(text="hi")])], captured)
        decl = captured["model_kwargs"]["tools"][0]["function_declarations"][0]
        assert "additionalProperties" not in decl["parameters"]
        assert "maxLength" not in decl["parameters"]["properties"]["q"]

    def test_clean_schema_recursive(self):
        cleaned = _clean_schema(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"type": "string", "maxLength": 10}},
            }
        )
        assert cleaned == {"type": "object", "properties": {"x": {"type": "string"}}}

    def test_tool_result_wire(self):
        wire = gemini_to_wire(
            [
                {
                    "role": "tool_result",
                    "tool_call_id": "get_wallet_balance-0",
                    "tool_name": "get_wallet_balance",
                    "content": '{"balance": "42.50"}',
                }
            ]
        )
        part = wire[0]["parts"][0]
        assert part["function_response"]["name"] == "get_wallet_balance"
        assert part["function_response"]["response"] == {"balance": "42.50"}

    def test_assistant_tool_call_wire(self):
        wire = gemini_to_wire(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [ToolCall(id="x-0", name="x", arguments={"a": 1})],
                }
            ]
        )
        assert wire[0]["role"] == "model"
        assert wire[0]["parts"][0]["function_call"] == {"name": "x", "args": {"a": 1}}
