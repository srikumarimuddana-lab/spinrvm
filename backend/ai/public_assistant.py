"""Anonymous AI assistant for the public spinr.ca website.

The marketing site's chat widget used to run its own retrieval stack against
its own copy of the help content, so a question answered one way in the app
could be answered another way on the website. This module removes that split:
the website now reaches the SAME provider, model and FAQ corpus as the in-app
assistant, chosen in admin → Settings → AI Assistant.

Why this is not just ``orchestrator.run_chat_turn``
---------------------------------------------------
The orchestrator is built around a signed-in user. It persists every turn to
``ai_conversations``/``ai_messages`` — whose ``user_id`` is NOT NULL and a
foreign key to ``users(id)``, and whose ``audience`` is CHECKed against
('rider','driver') — and it meters a per-user daily cap keyed on ``user["id"]``.
A website visitor has no user row, and minting synthetic ones for anonymous
traffic would be both an FK problem and a PIPEDA data-minimization failure
(we would be storing conversations for people who never signed up).

So this is a separate, deliberately STATELESS path, following the precedent
already set by ai/support_assistant.py: reuse the shared provider factory and
the shared tool handlers, own nothing durable. Nothing is written to the
database — history rides along on the request and is dropped when it ends.

What it reuses (so the two surfaces cannot drift):
  - ``providers.get_adapter()`` — same provider/model/key as the in-app chat
  - ``prompts.build_system_prompt(settings, "web")`` — the public persona
  - ``tools.execute_tool(..., audience="web")`` — the SAME search_faqs handler,
    semantic-search settings and FAQ rows the app reads

Safety posture:
  - The tool registry, not this module, decides what an anonymous turn may
    call. ``tool_defs_for("web")`` resolves to search_faqs + get_company_info
    only; every account/ride/booking tool is rider/driver-only, and
    execute_tool re-checks the audience before dispatching. There is no
    ``user`` to scope data to, so there is no user data to leak.
  - Visitor text is PII-scrubbed before it reaches the provider.
  - Prompt-injection attempts are recorded to the security console, same
    tripwire as the in-app path.
  - Provider/config failures surface loudly (Sentry + a typed error) — never
    a silent canned answer.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

try:
    from .pii import filter_tool_leakage, scrub_pii
    from .prompts import build_system_prompt
    from .providers import get_adapter
    from .providers.base import AIConfigError
    from .threat import record_security_event, scan_message
    from .tools import execute_tool, tool_defs_for
except ImportError:  # pragma: no cover — direct module import (python -m backend.server)
    from ai.pii import filter_tool_leakage, scrub_pii  # type: ignore
    from ai.prompts import build_system_prompt  # type: ignore
    from ai.providers import get_adapter  # type: ignore
    from ai.providers.base import AIConfigError  # type: ignore
    from ai.threat import record_security_event, scan_message  # type: ignore
    from ai.tools import execute_tool, tool_defs_for  # type: ignore

try:
    from ..settings_loader import get_app_settings
    from ..utils.metrics import inc as _metric_inc
except ImportError:  # pragma: no cover
    from settings_loader import get_app_settings  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore

logger = logging.getLogger(__name__)

# The tool-registry audience for anonymous website turns. Must match the
# audience the read-only tools opt into in ai/tools_support.py.
WEB_AUDIENCE = "web"

GENERIC_ERROR_MESSAGE = "Something went wrong on our side — please try again in a moment."
NO_ANSWER_MESSAGE = "I couldn't find an answer to that one. Email support@spinr.ca and the team will help."

# Bounds on client-supplied history. The website sends the transcript back on
# every turn (nothing is stored server-side), so these are the only thing
# stopping a crafted request from stuffing the provider context. Kept tighter
# than the in-app ai_history_max_messages: an anonymous turn is unauthenticated
# and metered only by IP.
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_MESSAGE_CHARS = 2000

# Tool-loop bounds. Two iterations is enough for the only shape this audience
# can produce (search_faqs → answer, occasionally a second lookup); the FAQ
# tools hit our own database, not a paid upstream, so the ceiling is about
# bounding latency and provider spend rather than API cost.
MAX_TOOL_ITERATIONS = 3
MAX_TOOL_CALLS_PER_ITERATION = 3


class PublicAssistantError(Exception):
    """A public turn could not be completed. ``code`` maps to an HTTP status
    in routes/ai.py (disabled -> 503, misconfigured -> 503, provider -> 502)."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _capture(exc: Exception) -> None:
    """Sentry capture with the conventional tags; never raises.

    No user/rider/driver id tag — this surface is anonymous by construction,
    and there is no id to attach that would not be invented.
    """
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("domain", "ai")
            scope.set_tag("surface", "backend")
            scope.set_tag("ai_audience", WEB_AUDIENCE)
            sentry_sdk.capture_exception(exc)
    except Exception as _sentry_exc:  # pragma: no cover — sentry optional in dev
        logger.debug("sentry capture skipped: %s", _sentry_exc)


def _clean_history(history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    """Normalize client-supplied history into canonical user/assistant turns.

    Everything here is attacker-controlled: the browser can send any roles,
    any lengths, any count. Anything that is not a plain user/assistant text
    message is DROPPED rather than repaired — in particular ``tool_calls`` and
    ``tool_result`` rows, which a caller could otherwise use to fabricate a
    tool result the model would treat as ground truth about Spinr's pricing.
    Only the most recent MAX_HISTORY_MESSAGES survive.
    """
    if not history:
        return []
    cleaned: List[Dict[str, str]] = []
    for item in history[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        # Past visitor turns are scrubbed on the way back in too — the client
        # is echoing text we scrubbed last turn, but it is free to echo
        # anything, so we never trust it to have stayed scrubbed.
        text = scrub_pii(content.strip()[:MAX_HISTORY_MESSAGE_CHARS])
        cleaned.append({"role": role, "content": text})
    return cleaned


def _tool_result_payload(result: Any) -> Any:
    """Strip orchestrator-only meta keys before a tool result reaches the model.

    ``_client_action`` drives an in-app UI card and ``_no_cache`` marks a turn
    non-replayable for the cross-user response cache. Neither exists on this
    surface (no app UI, no cache), and both are internal — they must never be
    serialized into the model's context.
    """
    if not isinstance(result, dict):
        return result
    return {k: v for k, v in result.items() if k not in ("_client_action", "_no_cache")}


async def run_public_turn(
    *,
    message: str,
    history: Optional[List[Dict[str, Any]]] = None,
    visitor_type: str = "rider",
) -> Dict[str, Any]:
    """Run one anonymous website chat turn and return the finished reply.

    Non-streaming by design: the website widget renders a whole answer, and a
    single JSON response keeps the public surface free of the SSE keep-alive
    machinery the app clients need.

    Raises PublicAssistantError for every failure mode — the caller maps
    ``code`` to a status. There is no canned-answer fallback here; a broken
    provider must be visible, not papered over (CLAUDE.md: do not silently
    swallow errors).
    """
    settings = await get_app_settings()

    # Two switches, both must be on. ai_assistant_enabled is the global AI
    # kill switch shared with the apps; ai_public_chat_enabled additionally
    # gates THIS surface, so the website can be turned off on its own without
    # taking the rider/driver assistant down with it (and so the feature can
    # ship dark — it defaults off).
    if not settings.get("ai_assistant_enabled"):
        _metric_inc("spinr_ai_public_turns_total", {"outcome": "disabled"})
        raise PublicAssistantError("ai_disabled", "The assistant is currently unavailable.")
    if not settings.get("ai_public_chat_enabled"):
        _metric_inc("spinr_ai_public_turns_total", {"outcome": "disabled"})
        raise PublicAssistantError("ai_disabled", "The assistant is currently unavailable.")

    # Threat tripwire — detection only, same as the in-app path. Records signal
    # tags for the security console, never the message text. No user id or
    # conversation id exists on this surface; the source tag distinguishes it.
    threat_hit = scan_message(message)
    if threat_hit:
        signals = threat_hit["signals"]
        event_type = next((s for s in signals if s in ("impersonation", "data_exfiltration")), signals[0])
        record_security_event(
            user_id=None,
            audience=WEB_AUDIENCE,
            conversation_id=None,
            event_type=event_type,
            severity=threat_hit["severity"],
            signals=signals,
            source="public_web",
        )

    # Strict scrub (no keep_trip_pins): the public site has no map-pin or
    # quote-card flow, so bracketed coordinates here are never app-generated.
    scrubbed = scrub_pii(message.strip())

    try:
        adapter = await get_adapter()
    except AIConfigError as exc:
        logger.error("public ai adapter misconfigured: %s", exc)
        _capture(exc)
        _metric_inc("spinr_ai_provider_errors_total", {"provider": getattr(exc, "provider", "unknown")})
        _metric_inc("spinr_ai_public_turns_total", {"outcome": "error"})
        raise PublicAssistantError("ai_misconfigured", GENERIC_ERROR_MESSAGE) from exc

    system = build_system_prompt(settings, WEB_AUDIENCE)
    tools = tool_defs_for(WEB_AUDIENCE)
    messages: List[Dict[str, Any]] = [*_clean_history(history), {"role": "user", "content": scrubbed}]

    # The synthetic caller. It carries NO identity — deliberately no "id" key,
    # so any handler that reached for one would raise loudly rather than read
    # someone else's row by accident. _web_visitor_type only picks which FAQ
    # rows to search (rider-tagged vs driver-tagged); it grants nothing.
    tool_user: Dict[str, Any] = {
        "_web_visitor_type": visitor_type if visitor_type in ("rider", "driver") else "rider",
        "_client_capabilities": frozenset(),
    }

    all_text: List[str] = []
    tools_used: List[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0}

    try:
        for _iteration in range(MAX_TOOL_ITERATIONS):
            turn_text: List[str] = []
            turn_end = None
            async for event in adapter.stream_turn(system=system, messages=messages, tools=tools):
                if event.type == "text" and event.text:
                    turn_text.append(event.text)
                elif event.type == "turn_end":
                    turn_end = event
            if turn_end is None:  # adapter contract violation — surface loudly
                raise RuntimeError(f"adapter {adapter.provider} ended stream without turn_end")

            all_text.extend(turn_text)
            for key in usage:
                usage[key] += int((turn_end.usage or {}).get(key, 0) or 0)

            tool_calls = turn_end.tool_calls or []
            if turn_end.stop_reason != "tool_use" or not tool_calls:
                break

            messages.append({"role": "assistant", "content": "".join(turn_text), "tool_calls": tool_calls})

            # Over-budget calls are refused, never dropped: each gets a
            # synthetic error result so the model sees the refusal and can
            # still reach a final answer this turn.
            executable = tool_calls[:MAX_TOOL_CALLS_PER_ITERATION]
            excess = tool_calls[MAX_TOOL_CALLS_PER_ITERATION:]
            if excess:
                logger.warning(
                    "public ai tool call budget exceeded: %d requested, %d executed",
                    len(tool_calls),
                    len(executable),
                    # tool names only — never arguments/results (PIPEDA)
                    extra={"requested_tools": [tc.name for tc in tool_calls]},
                )
                _metric_inc("spinr_ai_tool_calls_capped_total", by=len(excess))

            executed: List[Tuple[Any, bool]] = list(
                await asyncio.gather(
                    *(execute_tool(tc.name, tc.arguments, user=tool_user, audience=WEB_AUDIENCE) for tc in executable)
                )
            )
            refused: List[Tuple[Any, bool]] = [
                (
                    {
                        "error": (
                            f"tool call budget exceeded — only {MAX_TOOL_CALLS_PER_ITERATION} tool "
                            "calls are allowed per turn; this call was not executed"
                        )
                    },
                    False,
                )
                for _ in excess
            ]

            for tc, (result, ok) in zip(tool_calls, executed + refused, strict=True):
                tools_used.append(tc.name)
                _metric_inc("spinr_ai_tool_calls_total", {"tool": tc.name, "ok": str(ok).lower()})
                messages.append(
                    {
                        "role": "tool_result",
                        "tool_call_id": tc.id,
                        "tool_name": tc.name,
                        "content": json.dumps(_tool_result_payload(result), default=str),
                        "is_error": not ok,
                    }
                )
        else:
            logger.warning("public ai tool loop hit iteration cap")
    except Exception as exc:
        logger.error("public ai turn failed", exc_info=True)
        _capture(exc)
        _metric_inc("spinr_ai_provider_errors_total", {"provider": getattr(adapter, "provider", "unknown")})
        _metric_inc("spinr_ai_public_turns_total", {"outcome": "error"})
        raise PublicAssistantError("provider_error", GENERIC_ERROR_MESSAGE) from exc

    # filter_tool_leakage only — NOT scrub_pii. The reply legitimately carries
    # Spinr's own support phone and email (get_company_info, and the contact
    # tail build_system_prompt appends), and scrubbing would redact exactly the
    # thing the visitor asked for. Nothing user-identifying can reach here: the
    # only tools this audience can call return FAQ rows and company contact
    # details, both public marketing content.
    reply = filter_tool_leakage("".join(all_text).strip())
    if not reply:
        reply = NO_ANSWER_MESSAGE
        _metric_inc("spinr_ai_public_turns_total", {"outcome": "empty"})
    else:
        _metric_inc("spinr_ai_public_turns_total", {"outcome": "completed"})

    return {
        "reply": reply,
        "tools_used": tools_used,
        "usage": usage,
        "provider": getattr(adapter, "provider", None),
        "model": getattr(adapter, "model", None),
    }
