"""Gemini adapter (google-generativeai — already in the backend lockfile).

Gemini's function-calling schema accepts only an OpenAPI subset, so JSON
Schema keywords it rejects are stripped recursively. Gemini has no tool-call
ids — they are synthesized as "{name}-{index}" and mapped back via the
function_response part (keyed by name, not id).
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, List

try:
    from .base import BaseLLMAdapter, ChatMessage, StreamEvent, ToolCall, ToolDef
except ImportError:
    from ai.providers.base import BaseLLMAdapter, ChatMessage, StreamEvent, ToolCall, ToolDef

logger = logging.getLogger(__name__)

# Keywords Gemini's FunctionDeclaration schema rejects.
_UNSUPPORTED_SCHEMA_KEYS = {"additionalProperties", "maxLength", "minLength", "examples", "default"}


def _clean_schema(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _clean_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


def _tools_to_wire(tools: List[ToolDef]) -> List[Dict[str, Any]]:
    if not tools:
        return []
    return [
        {
            "function_declarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": _clean_schema(t["input_schema"]),
                }
                for t in tools
            ]
        }
    ]


def _to_wire(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
    contents: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": m.get("content", "")}]})
        elif role == "assistant":
            parts: List[Dict[str, Any]] = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            for tc in m.get("tool_calls") or []:
                parts.append({"function_call": {"name": tc.name, "args": tc.arguments}})
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})
        elif role == "tool_result":
            try:
                response = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                response = {"result": m.get("content", "")}
            if not isinstance(response, dict):
                response = {"result": response}
            contents.append(
                {
                    "role": "user",
                    "parts": [{"function_response": {"name": m.get("tool_name", ""), "response": response}}],
                }
            )
    return contents


class GeminiAdapter(BaseLLMAdapter):
    provider = "gemini"

    async def stream_turn(
        self, *, system: str, messages: List[ChatMessage], tools: List[ToolDef]
    ) -> AsyncIterator[StreamEvent]:
        import google.generativeai as genai  # lazy

        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system,
            tools=_tools_to_wire(tools) or None,
            generation_config={"max_output_tokens": self.max_tokens},
        )

        tool_calls: List[ToolCall] = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        hit_max_tokens = False

        response = await model.generate_content_async(_to_wire(messages), stream=True)
        async for chunk in response:
            meta = getattr(chunk, "usage_metadata", None)
            if meta is not None:
                usage = {
                    "input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
                    "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
                }
            for candidate in getattr(chunk, "candidates", None) or []:
                if str(getattr(candidate, "finish_reason", "")).endswith("MAX_TOKENS"):
                    hit_max_tokens = True
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    fc = getattr(part, "function_call", None)
                    if fc is not None and getattr(fc, "name", None):
                        args = dict(getattr(fc, "args", None) or {})
                        tool_calls.append(ToolCall(id=f"{fc.name}-{len(tool_calls)}", name=fc.name, arguments=args))
                    elif getattr(part, "text", None):
                        yield StreamEvent(type="text", text=part.text)

        if tool_calls:
            stop = "tool_use"
        elif hit_max_tokens:
            stop = "max_tokens"
        else:
            stop = "end_turn"
        yield StreamEvent(type="turn_end", tool_calls=tool_calls or None, stop_reason=stop, usage=usage)
