"""Anthropic adapter (default provider — Claude Haiku).

Tools pass through 1:1 (Anthropic's native shape IS our canonical shape).
The system prompt carries a cache_control breakpoint; tool ordering is
already deterministic (registry sorts by name) so repeat turns hit the
prompt cache when the prefix is large enough.

The ``anthropic`` SDK is imported lazily inside stream_turn so the backend
runs without it installed (feature ships dark until the lockfile includes
the SDK — see requirements.in).
"""

from typing import Any, AsyncIterator, Dict, List

try:
    from .base import BaseLLMAdapter, ChatMessage, StreamEvent, ToolCall, ToolDef
except ImportError:
    from ai.providers.base import BaseLLMAdapter, ChatMessage, StreamEvent, ToolCall, ToolDef


def _to_wire(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
    """Canonical messages → Anthropic Messages API format."""
    wire: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            wire.append({"role": "user", "content": m.get("content", "")})
        elif role == "assistant":
            tool_calls = m.get("tool_calls") or []
            if not tool_calls:
                wire.append({"role": "assistant", "content": m.get("content", "")})
                continue
            blocks: List[Dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            wire.append({"role": "assistant", "content": blocks})
        elif role == "tool_result":
            # Consecutive same-role user messages are merged by the API.
            wire.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id", ""),
                            "content": m.get("content", ""),
                            "is_error": bool(m.get("is_error", False)),
                        }
                    ],
                }
            )
    return wire


class AnthropicAdapter(BaseLLMAdapter):
    provider = "anthropic"

    async def stream_turn(
        self, *, system: str, messages: List[ChatMessage], tools: List[ToolDef]
    ) -> AsyncIterator[StreamEvent]:
        import anthropic  # lazy — see module docstring

        wire_tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools
        ]
        async with anthropic.AsyncAnthropic(api_key=self.api_key) as client:
            async with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=wire_tools,
                messages=_to_wire(messages),
            ) as stream:
                async for event in stream:
                    if (
                        getattr(event, "type", "") == "content_block_delta"
                        and getattr(getattr(event, "delta", None), "type", "") == "text_delta"
                    ):
                        yield StreamEvent(type="text", text=event.delta.text)
                final = await stream.get_final_message()

        tool_calls = [
            ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
            for block in final.content
            if getattr(block, "type", "") == "tool_use"
        ]
        if final.stop_reason == "tool_use":
            stop = "tool_use"
        elif final.stop_reason == "max_tokens":
            stop = "max_tokens"
        else:
            stop = "end_turn"
        usage = {
            "input_tokens": getattr(final.usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(final.usage, "output_tokens", 0) or 0,
        }
        yield StreamEvent(type="turn_end", tool_calls=tool_calls or None, stop_reason=stop, usage=usage)
