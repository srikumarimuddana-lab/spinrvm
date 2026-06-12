"""OpenAI adapter — also serves OpenRouter via base_url override.

OpenRouter exposes an OpenAI-compatible chat-completions API, so the
factory constructs this same class with
base_url="https://openrouter.ai/api/v1" and the OpenRouter key; model ids
are then vendor-prefixed strings like "anthropic/claude-haiku-4.5".

Tool-call arguments stream as string fragments across chunks — they are
accumulated per index and parsed once at turn end. The ``openai`` SDK is
imported lazily (see requirements.in).
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, List

try:
    from .base import BaseLLMAdapter, ChatMessage, StreamEvent, ToolCall, ToolDef
except ImportError:
    from ai.providers.base import BaseLLMAdapter, ChatMessage, StreamEvent, ToolCall, ToolDef

logger = logging.getLogger(__name__)


def _to_wire(system: str, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
    wire: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        role = m.get("role")
        if role == "user":
            wire.append({"role": "user", "content": m.get("content", "")})
        elif role == "assistant":
            tool_calls = m.get("tool_calls") or []
            if not tool_calls:
                wire.append({"role": "assistant", "content": m.get("content", "")})
                continue
            wire.append(
                {
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in tool_calls
                    ],
                }
            )
        elif role == "tool_result":
            # No native error flag — the serialized content already carries
            # an "error" key when the handler failed.
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }
            )
    return wire


def _tools_to_wire(tools: List[ToolDef]) -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


class OpenAIAdapter(BaseLLMAdapter):
    provider = "openai"

    async def stream_turn(
        self, *, system: str, messages: List[ChatMessage], tools: List[ToolDef]
    ) -> AsyncIterator[StreamEvent]:
        import openai  # lazy — see module docstring

        client_kwargs: Dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        # tool-call fragments accumulated by stream index
        pending: Dict[int, Dict[str, str]] = {}
        finish_reason = None
        usage = {"input_tokens": 0, "output_tokens": 0}

        async with openai.AsyncOpenAI(**client_kwargs) as client:
            stream = await client.chat.completions.create(
                model=self.model,
                # max_completion_tokens (not the deprecated max_tokens): newer
                # OpenAI models (o1/o3/gpt-5 family) reject max_tokens with a 400
                # "unsupported_parameter"; gpt-4o/4o-mini accept the new name too.
                max_completion_tokens=self.max_tokens,
                messages=_to_wire(system, messages),
                tools=_tools_to_wire(tools) or None,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = {
                        "input_tokens": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                        "output_tokens": getattr(chunk_usage, "completion_tokens", 0) or 0,
                    }
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                if getattr(delta, "content", None):
                    yield StreamEvent(type="text", text=delta.content)
                for frag in getattr(delta, "tool_calls", None) or []:
                    idx = getattr(frag, "index", 0) or 0
                    slot = pending.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if getattr(frag, "id", None):
                        slot["id"] = frag.id
                    fn = getattr(frag, "function", None)
                    if fn is not None:
                        if getattr(fn, "name", None):
                            slot["name"] += fn.name
                        if getattr(fn, "arguments", None):
                            slot["arguments"] += fn.arguments

        tool_calls: List[ToolCall] = []
        for idx in sorted(pending):
            slot = pending[idx]
            try:
                arguments = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                logger.error("openai adapter: unparseable tool arguments", extra={"tool": slot["name"]})
                arguments = {}
            tool_calls.append(
                ToolCall(id=slot["id"] or f"{slot['name']}-{idx}", name=slot["name"], arguments=arguments)
            )

        if finish_reason == "tool_calls" or (tool_calls and finish_reason != "length"):
            stop = "tool_use"
        elif finish_reason == "length":
            stop = "max_tokens"
        else:
            stop = "end_turn"
        yield StreamEvent(type="turn_end", tool_calls=tool_calls or None, stop_reason=stop, usage=usage)
