"""Canonical LLM types + adapter ABC.

One adapter call = ONE model turn: it streams text deltas and finishes with
exactly one ``turn_end`` event carrying stop reason, usage and any tool
calls. The orchestrator owns the loop (execute tools → append tool_result
messages → next turn), so iteration caps, persistence and SSE emission stay
identical across providers.

SDKs are imported lazily inside each adapter's stream_turn — the backend
boots and tests run with none of the vendor SDKs installed (the feature
ships dark until the lockfile is regenerated; see requirements.in).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, ClassVar, Dict, List, Literal, Optional, TypedDict


class AIConfigError(Exception):
    """AI assistant is enabled but misconfigured (missing key / bad provider).

    Surfaces loudly (Sentry + SSE error frame) — never a silent fallback.
    """

    def __init__(self, provider: str, detail: str):
        self.provider = provider
        super().__init__(f"AI provider '{provider}' misconfigured: {detail}")


class ToolDef(TypedDict):
    name: str
    description: str
    input_schema: Dict[str, Any]  # JSON Schema (type=object)


@dataclass
class ToolCall:
    id: str  # provider call id (synthesized where the vendor has none)
    name: str
    arguments: Dict[str, Any]  # always a parsed dict, never a raw JSON string


class ChatMessage(TypedDict, total=False):
    """Canonical message. Adapters translate to/from wire format.

    roles:
      user        — content: str
      assistant   — content: str, optional tool_calls
      tool_result — content: str (serialized JSON), tool_call_id, tool_name, is_error
    """

    role: Literal["user", "assistant", "tool_result"]
    content: str
    tool_calls: List[ToolCall]
    tool_call_id: str
    tool_name: str
    is_error: bool


@dataclass
class StreamEvent:
    type: Literal["text", "turn_end"]
    text: str = ""
    tool_calls: Optional[List[ToolCall]] = None  # set on turn_end when the model requested tools
    stop_reason: Optional[Literal["end_turn", "tool_use", "max_tokens"]] = None
    usage: Dict[str, int] = field(default_factory=dict)  # {"input_tokens": n, "output_tokens": n}


class BaseLLMAdapter(ABC):
    provider: ClassVar[str] = "base"

    def __init__(self, *, api_key: str, model: str, max_tokens: int, base_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url

    @abstractmethod
    def stream_turn(
        self, *, system: str, messages: List[ChatMessage], tools: List[ToolDef]
    ) -> AsyncIterator[StreamEvent]:
        """Stream one model turn: zero or more ``text`` events, then exactly
        one ``turn_end``. Provider/network failures raise — the orchestrator
        converts them into an SSE error frame + Sentry capture."""
        raise NotImplementedError
