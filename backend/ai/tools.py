"""Tool registry for the AI assistant — single source of truth.

Two consumers:
1. The in-process chat loop (backend/ai/orchestrator.py) via execute_tool().
2. The optional /mcp mount (backend/ai/mcp_server.py), which auto-registers
   every spec with mcp_exposed=True so the surfaces cannot drift.

Safety contract (see CLAUDE.md + plan):
- Handlers receive the authenticated ``user`` dict injected server-side; the
  model can never pick whose data a tool reads. Handlers must re-verify
  ownership of any id-style argument (foreign id → "not found").
- Arguments are validated against the spec's JSON schema; unknown keys are
  rejected. Failures come back as model-readable error results — they never
  crash the chat stream.
- Results are size-capped before they re-enter the model context.
- Tool names + user_id may be logged; arguments and results never (they can
  contain addresses/balances — PIPEDA).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)

TOOL_TIMEOUT_SECONDS = 5.0
TOOL_RESULT_MAX_CHARS = 4000

_JSON_TYPES: Dict[str, tuple] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str  # prescriptive "call this when…" phrasing — providers pass it through
    input_schema: Dict[str, Any]  # JSON Schema (type=object)
    handler: Callable[..., Awaitable[Dict[str, Any]]]  # async def handler(user: dict, **args) -> dict
    audiences: frozenset = field(default_factory=lambda: frozenset({"rider"}))
    # Booking-flow tools are chat-only: the /mcp surface stays read-only.
    mcp_exposed: bool = True


TOOL_REGISTRY: Dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in TOOL_REGISTRY:
        raise ValueError(f"duplicate tool name: {spec.name}")
    TOOL_REGISTRY[spec.name] = spec
    return spec


# Domain handler modules that self-register on import. Extended as handler
# modules land (tools_rides, tools_money, tools_support, tools_booking).
_DOMAIN_MODULES: tuple = ()

_registry_loaded = False


def ensure_registry_loaded() -> None:
    """Import the domain handler modules exactly once.

    They self-register into TOOL_REGISTRY on import. Kept lazy so importing
    backend.ai.tools (e.g. from the MCP mount or tests) has no heavier
    side effects than the handler modules themselves.
    """
    global _registry_loaded
    if _registry_loaded:
        return
    import importlib

    for mod in _DOMAIN_MODULES:
        try:
            importlib.import_module(f".{mod}", __package__)
        except ImportError:
            importlib.import_module(f"ai.{mod}")
    _registry_loaded = True


def tool_defs_for(audience: str) -> List[Dict[str, Any]]:
    """Provider-ready tool definitions, sorted by name (deterministic for
    prompt caching)."""
    ensure_registry_loaded()
    return [
        {"name": s.name, "description": s.description, "input_schema": s.input_schema}
        for s in sorted(TOOL_REGISTRY.values(), key=lambda s: s.name)
        if audience in s.audiences
    ]


def validate_args(schema: Dict[str, Any], args: Dict[str, Any]) -> List[str]:
    """Minimal JSON-schema validation: required keys, no extras, primitive
    types, enums, integer bounds. Returns a list of human-readable errors
    (empty = valid). Deliberately dependency-free — jsonschema is not in the
    backend lockfile."""
    errors: List[str] = []
    if not isinstance(args, dict):
        return ["arguments must be an object"]

    properties: Dict[str, Any] = schema.get("properties", {})
    for key in args:
        if key not in properties:
            errors.append(f"unknown argument: {key}")
    for key in schema.get("required", []):
        if key not in args:
            errors.append(f"missing required argument: {key}")

    for key, value in args.items():
        prop = properties.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        allowed = _JSON_TYPES.get(expected)
        if allowed:
            # bool is an int subclass — keep integer/number strict.
            if isinstance(value, bool) and expected in ("integer", "number"):
                errors.append(f"{key} must be a {expected}")
                continue
            if not isinstance(value, allowed):
                errors.append(f"{key} must be a {expected}")
                continue
        if "enum" in prop and value not in prop["enum"]:
            errors.append(f"{key} must be one of {prop['enum']}")
        if expected in ("integer", "number"):
            if "minimum" in prop and value < prop["minimum"]:
                errors.append(f"{key} must be >= {prop['minimum']}")
            if "maximum" in prop and value > prop["maximum"]:
                errors.append(f"{key} must be <= {prop['maximum']}")
        if expected == "string":
            max_len = prop.get("maxLength", 500)
            if len(value) > max_len:
                errors.append(f"{key} must be at most {max_len} characters")
    return errors


def _cap_result(result: Dict[str, Any]) -> Dict[str, Any]:
    serialized = json.dumps(result, default=str)
    if len(serialized) <= TOOL_RESULT_MAX_CHARS:
        return result
    return {"_truncated": True, "preview": serialized[:TOOL_RESULT_MAX_CHARS]}


async def execute_tool(
    name: str, args: Dict[str, Any], *, user: Dict[str, Any], audience: str = "rider"
) -> Tuple[Dict[str, Any], bool]:
    """Run one tool call. Returns (result, ok).

    ``audience`` is decided by the calling surface (chat route / MCP auth),
    never by the model. Never raises for model-recoverable problems —
    unknown tool, bad args, handler failure and timeout all come back as
    (error-result, False) so the chat turn keeps streaming. Real defects
    are logged loudly here.
    """
    ensure_registry_loaded()
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return {"error": f"unknown tool: {name}"}, False

    if audience not in spec.audiences:
        return {"error": f"tool not available: {name}"}, False

    errors = validate_args(spec.input_schema, args or {})
    if errors:
        return {"error": "; ".join(errors)}, False

    try:
        result = await asyncio.wait_for(spec.handler(user, **(args or {})), timeout=TOOL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error("ai tool timed out", extra={"tool": name, "user_id": user.get("id")})
        return {"error": "the lookup took too long — try again"}, False
    except Exception:
        logger.error("ai tool failed", exc_info=True, extra={"tool": name, "user_id": user.get("id")})
        return {"error": "the lookup failed — try again or contact support"}, False

    return _cap_result(result), True
