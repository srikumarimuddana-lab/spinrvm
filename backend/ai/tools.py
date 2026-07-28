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
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover — top-level run
    import db_supabase

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


# Identity arguments the model must NEVER supply. The caller's identity is
# injected server-side from the authenticated session (execute_tool's ``user``)
# and is the ONLY id a query may be scoped to — PIPEDA data-minimization: the
# assistant can only ever read the signed-in user's own data. These names are
# banned two ways: a tool may not even *declare* one (checked in register()),
# and a live call carrying one is rejected (checked in execute_tool()).
FORBIDDEN_ID_ARGS = frozenset(
    {
        "user_id",
        "rider_id",
        "driver_id",
        "customer_id",
        "account_id",
        "owner_id",
        "actor_id",
        "admin_id",
        "uid",
        "user",
        "rider",
        "driver",
    }
)

# Ownership verifiers for owned-resource id args (e.g. "ride"). Each returns
# True only when ``resource_id`` belongs to ``user_id``. Domain modules register
# theirs on import via register_ownership_verifier().
OWNERSHIP_VERIFIERS: Dict[str, Callable[[str, str], Awaitable[bool]]] = {}


def register_ownership_verifier(kind: str, fn: Callable[[str, str], Awaitable[bool]]) -> None:
    OWNERSHIP_VERIFIERS[kind] = fn


def _is_id_arg(name: str) -> bool:
    """An argument that names an entity id — must be classified (owned/public)
    or banned, never left unclassified."""
    return name == "id" or name.endswith("_id") or name in FORBIDDEN_ID_ARGS


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str  # prescriptive "call this when…" phrasing — providers pass it through
    input_schema: Dict[str, Any]  # JSON Schema (type=object)
    handler: Callable[..., Awaitable[Dict[str, Any]]]  # async def handler(user: dict, **args) -> dict
    audiences: frozenset = field(default_factory=lambda: frozenset({"rider"}))
    # Booking-flow tools are chat-only: the /mcp surface stays read-only.
    mcp_exposed: bool = True
    # Data-scope contract. Every id-style argument MUST be classified so no tool
    # can accept an id the caller doesn't own:
    #   owned_id_args:  arg name -> owned-resource kind (ownership verified
    #                   centrally against the caller before the handler runs)
    #   public_id_args: arg names that reference non-user data (e.g. a vehicle
    #                   type) and so need no ownership check
    # An id-style arg that is neither classified nor banned fails registration.
    owned_id_args: Dict[str, str] = field(default_factory=dict)
    public_id_args: frozenset = field(default_factory=frozenset)
    # Per-tool handler timeout. None -> the global TOOL_TIMEOUT_SECONDS. Only
    # the Maps fan-out tools (find_place, get_fare_quote, propose_ride_booking)
    # need more: their worst case chains concurrent 4 s geocode pairs plus a
    # 2 s Directions wait, which the 5 s default cut off mid-quote.
    timeout_seconds: Optional[float] = None


TOOL_REGISTRY: Dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in TOOL_REGISTRY:
        raise ValueError(f"duplicate tool name: {spec.name}")
    # Enforce the data-scope contract at import time so a mis-scoped tool can
    # never reach production: identity args are banned outright, and every other
    # id-style arg must be declared owned (ownership-verified) or public.
    for prop in spec.input_schema.get("properties") or {}:
        if prop in FORBIDDEN_ID_ARGS:
            raise ValueError(
                f"tool {spec.name!r} declares forbidden identity arg {prop!r} — "
                "the caller's identity is server-injected, never a tool argument"
            )
        if _is_id_arg(prop) and prop not in spec.owned_id_args and prop not in spec.public_id_args:
            raise ValueError(
                f"tool {spec.name!r} has unclassified id arg {prop!r} — add it to "
                "owned_id_args (with a verifier) or public_id_args"
            )
    TOOL_REGISTRY[spec.name] = spec
    return spec


# Domain handler modules that self-register on import. Extended as handler
# modules land (tools_rides, tools_money, tools_support, tools_booking).
_DOMAIN_MODULES: tuple = ("tools_rides", "tools_account", "tools_support", "tools_booking", "tools_driver")

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


# Keys that carry the model-facing guardrail instructions and refusal
# sentinels ("Do NOT quote on it", needs_correction=...). Truncation must
# never destroy these: a huge quote result that loses its note while its
# _client_action card survives would show the rider a card the model was
# explicitly told not to act on.
_GUARDRAIL_KEYS = ("note", "needs_confirmation", "needs_correction", "imprecise_address", "error")


def _cap_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Cap the MODEL-facing portion of a result. ``_client_action`` is popped
    by the orchestrator before the result enters the model context, so it
    neither counts against the budget nor gets destroyed by truncation —
    a rich quote card must survive even when the textual result is huge.
    Guardrail keys (notes, refusal sentinels) are re-attached after
    truncation for the same reason."""
    client_action = result.pop("_client_action", None) if isinstance(result, dict) else None
    serialized = json.dumps(result, default=str)
    if len(serialized) > TOOL_RESULT_MAX_CHARS:
        preserved = {k: result[k] for k in _GUARDRAIL_KEYS if isinstance(result, dict) and k in result}
        result = {"_truncated": True, "preview": serialized[:TOOL_RESULT_MAX_CHARS], **preserved}
    if client_action is not None:
        result["_client_action"] = client_action
    return result


def _classify_outcome(result: Dict[str, Any], ok: bool) -> str:
    """Bucket a tool result for the audit trail (no PII)."""
    if ok:
        return "success"
    err = (result or {}).get("error", "") if isinstance(result, dict) else ""
    if err in ("not authorized", "not permitted"):
        return "blocked"
    if err.endswith("not found"):
        return "not_found"
    if "took too long" in err:
        return "timeout"
    return "error"


def _schedule_tool_audit(record: Dict[str, Any]) -> None:
    """Fire-and-forget the audit write so it never adds latency to the tool
    path. PIPEDA-safe: the record carries argument NAMES only, never values or
    results (see migration 217). A write failure must never affect the chat."""

    async def _run() -> None:
        try:
            await db_supabase.insert_one("ai_tool_audit", record)
        except Exception:
            logger.error("ai tool-audit write failed", exc_info=True, extra={"tool": record.get("tool_name")})

    try:
        task = asyncio.create_task(_run())
        task.add_done_callback(lambda t: t.exception())
    except RuntimeError:
        # No running loop (e.g. sync test context) — skip; auditing is best-effort.
        pass


async def execute_tool(
    name: str, args: Dict[str, Any], *, user: Dict[str, Any], audience: str = "rider"
) -> Tuple[Dict[str, Any], bool]:
    """Run one tool call and audit it. Returns (result, ok).

    ``audience`` is decided by the calling surface (chat route / MCP auth),
    never by the model. Never raises for model-recoverable problems —
    unknown tool, bad args, handler failure and timeout all come back as
    (error-result, False) so the chat turn keeps streaming. Real defects
    are logged loudly here.

    Every call is recorded to ai_tool_audit (Layer 7 governance) — tool name,
    user_id, audience, outcome, latency and argument KEY NAMES only (never
    values/results — PIPEDA).
    """
    start = time.perf_counter()
    result, ok = await _execute_tool_inner(name, args, user=user, audience=audience)
    outcome = _classify_outcome(result, ok)
    # A misbehaving provider can emit non-object tool arguments (e.g. a JSON
    # list/string); collect key names only when we truly have a dict so the
    # audit path never turns a model-recoverable bad-args result into a crash.
    arg_keys = sorted(args.keys()) if isinstance(args, dict) else []
    _schedule_tool_audit(
        {
            "user_id": (user or {}).get("id"),
            "audience": audience,
            "tool_name": name,
            "ok": ok,
            "outcome": outcome,
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "arg_keys": arg_keys,
            "conversation_id": (user or {}).get("_conversation_id"),
        }
    )
    # A tool blocked by the identity/scoping guards is a clear attack signal —
    # surface it in the security feed, not just the audit trail.
    if outcome == "blocked":
        try:
            from .threat import record_security_event
        except ImportError:
            from threat import record_security_event
        record_security_event(
            user_id=(user or {}).get("id"),
            audience=audience,
            conversation_id=(user or {}).get("_conversation_id"),
            event_type="tool_blocked",
            severity="critical",
            signals=[name, *arg_keys],
            source="tool",
        )
    return result, ok


async def _execute_tool_inner(
    name: str, args: Dict[str, Any], *, user: Dict[str, Any], audience: str = "rider"
) -> Tuple[Dict[str, Any], bool]:
    ensure_registry_loaded()
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return {"error": f"unknown tool: {name}"}, False

    if audience not in spec.audiences:
        return {"error": f"tool not available: {name}"}, False

    call_args = args if isinstance(args, dict) else {}

    # Fail closed: no tool runs without an authenticated identity to scope to.
    user_id = (user or {}).get("id")
    if not user_id:
        logger.error("ai tool blocked: no authenticated user id", extra={"tool": name})
        return {"error": "not authorized"}, False

    # The model may never supply an identity id. This runs BEFORE schema
    # validation on purpose: for a real tool schema an identity key like
    # rider_id is an *unknown* argument, so validate_args would reject it as a
    # generic error and the security console would never see the attempt.
    # Checking first classifies the smuggled id as "not permitted" (blocked) so
    # the identity-theft attempt lands in ai_security_events.
    forbidden = FORBIDDEN_ID_ARGS & set(call_args)
    if forbidden:
        logger.error(
            "ai tool blocked: model supplied identity arg(s) %s",
            sorted(forbidden),
            extra={"tool": name, "user_id": user_id},
        )
        return {"error": "not permitted"}, False

    errors = validate_args(spec.input_schema, args if args is not None else {})
    if errors:
        return {"error": "; ".join(errors)}, False

    # Owned-resource id args must belong to the caller BEFORE the handler runs.
    # A foreign or missing id reads as "not found" — the model can never tell
    # "not yours" from "does not exist".
    for arg_name, kind in spec.owned_id_args.items():
        if arg_name not in call_args:
            continue
        verifier = OWNERSHIP_VERIFIERS.get(kind)
        if verifier is None:
            logger.error("ai tool blocked: no ownership verifier for %r", kind, extra={"tool": name})
            return {"error": f"{kind} not found"}, False
        try:
            owned = await asyncio.wait_for(verifier(str(call_args[arg_name]), user_id), timeout=TOOL_TIMEOUT_SECONDS)
        except Exception:
            logger.error("ai ownership check failed", exc_info=True, extra={"tool": name, "user_id": user_id})
            return {"error": "the lookup failed — try again"}, False
        if not owned:
            return {"error": f"{kind} not found"}, False

    # Audience-aware handlers (e.g. FAQ search) read it from the user dict;
    # it stays server-decided either way.
    handler_user = {**user, "ai_audience": audience}

    handler_timeout = spec.timeout_seconds if spec.timeout_seconds is not None else TOOL_TIMEOUT_SECONDS
    try:
        result = await asyncio.wait_for(spec.handler(handler_user, **call_args), timeout=handler_timeout)
    except asyncio.TimeoutError:
        logger.error("ai tool timed out", extra={"tool": name, "user_id": user.get("id")})
        return {"error": "the lookup took too long — try again"}, False
    except Exception:
        logger.error("ai tool failed", exc_info=True, extra={"tool": name, "user_id": user.get("id")})
        return {"error": "the lookup failed — try again or contact support"}, False

    return _cap_result(result), True
