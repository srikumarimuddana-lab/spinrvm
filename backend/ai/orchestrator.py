"""Provider-agnostic chat-turn orchestrator.

run_chat_turn() is an async generator of (event_name, payload) SSE frames:

    meta   {conversation_id, user_message_id}
    token  {text}                                  (repeated)
    tool   {name, status: start|end, ok}           (names only — never payloads)
    action {type, ...}                             (e.g. booking_proposal)
    done   {message_id, usage, stop_reason}
    error  {code, message}

It owns the tool loop: stream one adapter turn → execute requested tools →
append canonical tool_result messages → next turn, capped by
ai_max_tool_iterations. Provider/config failures surface loudly (Sentry +
error frame) — never a silent canned fallback (deliberate divergence from
routes/support.py).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

try:
    from . import conversations, response_cache
    from .pii import scrub_pii
    from .prompts import build_system_prompt
    from .providers import get_adapter
    from .providers.base import AIConfigError
    from .threat import record_security_event, scan_message
    from .tools import execute_tool, tool_defs_for
except ImportError:
    from ai import conversations, response_cache
    from ai.pii import scrub_pii
    from ai.prompts import build_system_prompt
    from ai.providers import get_adapter
    from ai.providers.base import AIConfigError
    from ai.threat import record_security_event, scan_message
    from ai.tools import execute_tool, tool_defs_for

try:
    from ..settings_loader import get_app_settings
    from ..utils.metrics import inc as _metric_inc
    from ..utils.redis_client import redis_expire, redis_incr
except ImportError:
    from settings_loader import get_app_settings
    from utils.metrics import inc as _metric_inc
    from utils.redis_client import redis_expire, redis_incr

logger = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "Something went wrong on our side — please try again in a moment."

Frame = Tuple[str, Dict[str, Any]]


def _capture(exc: Exception, user: Dict[str, Any]) -> None:
    """Sentry capture with the conventional tags; never raises."""
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("domain", "ai")
            scope.set_tag("surface", "backend")
            scope.set_tag("rider_id", user.get("id"))
            sentry_sdk.capture_exception(exc)
    except Exception as _sentry_exc:  # pragma: no cover — sentry optional in dev
        logger.debug("sentry capture skipped: %s", _sentry_exc)


def _coord(value: Any) -> Optional[str]:
    try:
        return f"{float(value):.5f}"
    except (TypeError, ValueError):
        return None


async def _pinned_quote_context(conversation_id: str) -> str:
    """Prompt tail naming the trip most recently priced in this conversation.

    Sits after the stable instruction block so provider prompt caching keeps
    working (same placement rule as the support-contact tail). Imported
    lazily to preserve tools_booking's lazy registration.
    """
    try:
        from .tools_booking import load_pinned_quote
    except ImportError:  # pragma: no cover — top-level run
        from ai.tools_booking import load_pinned_quote

    pinned = await load_pinned_quote(conversation_id)
    if not pinned:
        return ""
    pickup_lat, pickup_lng = _coord(pinned.get("pickup_lat")), _coord(pinned.get("pickup_lng"))
    dropoff_lat, dropoff_lng = _coord(pinned.get("dropoff_lat")), _coord(pinned.get("dropoff_lng"))
    if not all((pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)):
        return ""
    bits = [
        "\n\nLAST QUOTE IN THIS CONVERSATION (you priced this trip — reuse it, do NOT re-resolve it):",
        f"- pickup: {pinned.get('pickup_address') or 'unnamed'} [{pickup_lat},{pickup_lng}]",
        f"- dropoff: {pinned.get('dropoff_address') or 'unnamed'} [{dropoff_lat},{dropoff_lng}]",
    ]
    if pinned.get("vehicle_type_id"):
        bits.append(f"- vehicle_type_id: {pinned['vehicle_type_id']} ({pinned.get('vehicle_type') or 'recommended'})")
    if pinned.get("total"):
        bits.append(f"- quoted_total: {pinned['total']}")
    if pinned.get("promo_code"):
        bits.append(f"- promo_code: {pinned['promo_code']}")
    return "\n".join(bits)


async def _over_daily_cap(user_id: str, cap: int) -> bool:
    """Per-user daily message cap via Redis INCR. Fails OPEN with a loud log
    (mirrors the non-OTP rate-limit policy) — the kill switch remains the
    hard stop when Redis is down."""
    key = f"ai:daily:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    try:
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, 86400)
        return count > cap
    except Exception:
        logger.error("ai daily-cap check failed — failing open", exc_info=True, extra={"user_id": user_id})
        return False


async def run_chat_turn(
    *,
    user: Dict[str, Any],
    conversation_id: Optional[str],
    user_message: str,
    audience: str = "rider",
    admin_actor_id: Optional[str] = None,
    client_location: Optional[Dict[str, float]] = None,
    client_capabilities: Optional[list] = None,
) -> AsyncIterator[Frame]:
    settings = await get_app_settings()

    if not settings.get("ai_assistant_enabled"):
        _metric_inc("spinr_ai_chat_turns_total", {"outcome": "disabled"})
        yield "error", {"code": "ai_disabled", "message": "The AI assistant is currently unavailable."}
        return

    # Admin-console turns (super-admin-only, audited) don't count against —
    # or get blocked by — the impersonated user's daily cap: heavy console
    # testing was 429ing and silently draining the target rider's quota.
    if admin_actor_id is None:
        cap = int(settings.get("ai_daily_message_cap") or 50)
        if await _over_daily_cap(user["id"], cap):
            _metric_inc("spinr_ai_chat_turns_total", {"outcome": "capped"})
            yield (
                "error",
                {"code": "daily_cap", "message": "You've reached today's AI assistant limit — try again tomorrow."},
            )
            return

    conversation = await conversations.get_or_create_conversation(
        user["id"], conversation_id, audience, admin_actor_id=admin_actor_id
    )
    if conversation is None:
        yield "error", {"code": "not_found", "message": "Conversation not found."}
        return

    # Threat tripwire — flag likely injection/jailbreak/impersonation attempts
    # for the security console. Detection only (the tool layer blocks the harm);
    # scans the raw message but records only signal tags, never the text.
    threat_hit = scan_message(user_message)
    if threat_hit:
        signals = threat_hit["signals"]
        etype = next((s for s in signals if s in ("impersonation", "data_exfiltration")), signals[0])
        record_security_event(
            user_id=user.get("id"),
            audience=audience,
            conversation_id=conversation["id"],
            event_type=etype,
            severity=threat_hit["severity"],
            signals=signals,
            source="message",
        )

    # keep_trip_pins: chat messages may carry app-generated bracketed
    # [lat,lng] trip endpoints (quote-card taps, map-pin confirms) that the
    # model must see verbatim. Only this path opts in — Sentry and support
    # scrubbing stay fully strict.
    scrubbed = scrub_pii(user_message, keep_trip_pins=True)
    user_row = await conversations.append_message(conversation, "user", scrubbed)
    yield "meta", {"conversation_id": conversation["id"], "user_message_id": user_row["id"]}

    history = await conversations.load_history(conversation["id"], int(settings.get("ai_history_max_messages") or 12))

    # FAQ response cache — only first-message, non-admin turns are context-free
    # enough to replay. load_history already includes the just-appended user
    # row, so a fresh conversation has exactly one message (prior_turns == 0).
    prior_turns = max(len(history) - 1, 0)
    faq_cache_enabled = bool(settings.get("ai_faq_cache_enabled"))
    faq_cache_ttl = int(settings.get("ai_faq_cache_ttl_seconds") or 3600)
    # Threat-flagged turns must never enter (or be served from) the
    # cross-user FAQ cache: a crafted first message that slips a payload past
    # the scrubber would otherwise be replayed verbatim to other users.
    cache_eligible = faq_cache_enabled and admin_actor_id is None and prior_turns == 0 and not threat_hit
    if cache_eligible:
        cached = await response_cache.get_cached(audience, scrubbed)
        if cached is not None:
            _metric_inc("spinr_ai_response_cache_total", {"outcome": "hit"})
            yield "token", {"text": cached}
            assistant_row = await conversations.append_message(
                conversation, "assistant", cached, usage={"input_tokens": 0, "output_tokens": 0}, provider="cache"
            )
            _metric_inc("spinr_ai_chat_turns_total", {"outcome": "cached"})
            yield (
                "done",
                {
                    "message_id": assistant_row["id"],
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "stop_reason": "end_turn",
                },
            )
            return
        _metric_inc("spinr_ai_response_cache_total", {"outcome": "miss"})

    try:
        adapter = await get_adapter()
    except AIConfigError as exc:
        logger.error("ai adapter misconfigured: %s", exc, extra={"user_id": user.get("id")})
        _capture(exc, user)
        _metric_inc("spinr_ai_provider_errors_total", {"provider": getattr(exc, "provider", "unknown")})
        _metric_inc("spinr_ai_chat_turns_total", {"outcome": "error"})
        yield "error", {"code": "ai_misconfigured", "message": GENERIC_ERROR_MESSAGE}
        return

    system = build_system_prompt(settings, audience)
    # Replay the trip most recently priced in this conversation. Tool results
    # are never persisted, so without this a rider who types "book it" instead
    # of tapping the quote card leaves the model with no coordinates — it
    # re-resolves the destination and can price a different point than the one
    # it quoted (incident: CA$37.53 quote → CA$40.78 booking card).
    system += await _pinned_quote_context(conversation["id"])
    tools = tool_defs_for(audience)
    messages: List[Dict[str, Any]] = list(history)

    # Per-turn context rides along to tools only (never into the prompt, the
    # conversation rows, or logs): device location lets booking tools bias
    # place search / resolve "my location"; the conversation id lets
    # escalate_to_support attach a transcript to the ticket.
    tool_user = {**user, "_conversation_id": conversation["id"]}
    if client_location:
        tool_user["_client_location"] = client_location
    # UI features this client build can render (e.g. "map_pin" → the Drop-a-pin
    # card). Tools that emit client actions check this so an older installed
    # app is never promised a button it cannot draw.
    tool_user["_client_capabilities"] = frozenset(client_capabilities or ())

    max_iterations = int(settings.get("ai_max_tool_iterations") or 6)
    all_text: List[str] = []
    used_tool_names: List[str] = []
    emitted_client_action = False
    cache_disqualified = False
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    try:
        for _iteration in range(max_iterations):
            turn_text: List[str] = []
            turn_end = None
            async for event in adapter.stream_turn(system=system, messages=messages, tools=tools):
                if event.type == "text" and event.text:
                    turn_text.append(event.text)
                    yield "token", {"text": event.text}
                elif event.type == "turn_end":
                    turn_end = event
            if turn_end is None:  # adapter contract violation — surface loudly
                raise RuntimeError(f"adapter {adapter.provider} ended stream without turn_end")

            all_text.extend(turn_text)
            for k in total_usage:
                total_usage[k] += int((turn_end.usage or {}).get(k, 0) or 0)

            tool_calls = turn_end.tool_calls or []
            if turn_end.stop_reason != "tool_use" or not tool_calls:
                break

            messages.append({"role": "assistant", "content": "".join(turn_text), "tool_calls": tool_calls})
            for tc in tool_calls:
                yield "tool", {"name": tc.name, "status": "start"}
            results = await asyncio.gather(
                *(execute_tool(tc.name, tc.arguments, user=tool_user, audience=audience) for tc in tool_calls)
            )
            for tc, (result, ok) in zip(tool_calls, results, strict=True):
                used_tool_names.append(tc.name)
                _metric_inc("spinr_ai_tool_calls_total", {"tool": tc.name, "ok": str(ok).lower()})
                if isinstance(result, dict):
                    client_action = result.pop("_client_action", None)
                    # A tool whose answer varies per user (e.g. area-scoped FAQ
                    # search) marks the turn non-replayable so the cross-user
                    # response cache never serves one area's answer to another.
                    if result.pop("_no_cache", False):
                        cache_disqualified = True
                else:
                    client_action = None
                if client_action:
                    emitted_client_action = True
                    if client_action.get("type") == "booking_proposal":
                        _metric_inc("spinr_ai_booking_proposals_total")
                    yield "action", client_action
                yield "tool", {"name": tc.name, "status": "end", "ok": ok}
                messages.append(
                    {
                        "role": "tool_result",
                        "tool_call_id": tc.id,
                        "tool_name": tc.name,
                        "content": json.dumps(result, default=str),
                        "is_error": not ok,
                    }
                )
        else:
            # Tool budget exhausted without a final answer.
            logger.warning(
                "ai tool loop hit iteration cap",
                extra={"user_id": user.get("id"), "conversation_id": conversation["id"]},
            )
    except Exception as exc:
        logger.error("ai chat turn failed", exc_info=True, extra={"user_id": user.get("id")})
        _capture(exc, user)
        _metric_inc("spinr_ai_provider_errors_total", {"provider": getattr(adapter, "provider", "unknown")})
        _metric_inc("spinr_ai_chat_turns_total", {"outcome": "error"})
        yield "error", {"code": "provider_error", "message": GENERIC_ERROR_MESSAGE}
        return

    final_text = "".join(all_text).strip()
    produced_real_text = bool(final_text)  # False → the generic fallback below
    if not final_text:
        final_text = "I couldn't finish that one — could you rephrase, or tap Contact Support?"
        yield "token", {"text": final_text}

    # AI2 / PIPEDA: the user's message is scrubbed before persistence (line
    # ~186 above); the assistant's was not, despite the model being able to
    # echo tool-result data verbatim (e.g. a driver's phone number from a
    # dispatch tool result). Scrub only what's written to ai_messages / the
    # FAQ cache — the raw text has already streamed to the client this turn,
    # so the rider still sees the real reply; only stored/replayed copies
    # change. keep_trip_pins mirrors the user-side call in case the model
    # echoes a bracketed trip-endpoint pair back.
    stored_text = scrub_pii(final_text, keep_trip_pins=True)

    assistant_row = await conversations.append_message(
        conversation,
        "assistant",
        stored_text,
        tool_names=used_tool_names or None,
        usage=total_usage,
        provider=getattr(adapter, "provider", None),
        model=getattr(adapter, "model", None),
    )
    if (
        cache_eligible
        and produced_real_text
        and not cache_disqualified
        and response_cache.is_cacheable(
            admin_actor_id=admin_actor_id,
            prior_turns=prior_turns,
            used_tool_names=used_tool_names,
            had_client_action=emitted_client_action,
            text=final_text,
        )
    ):
        await response_cache.store_cached(audience, scrubbed, stored_text, faq_cache_ttl)
        _metric_inc("spinr_ai_response_cache_total", {"outcome": "store"})
    _metric_inc("spinr_ai_chat_turns_total", {"outcome": "completed"})
    _metric_inc("spinr_ai_tokens_total", {"direction": "input"}, by=total_usage["input_tokens"])
    _metric_inc("spinr_ai_tokens_total", {"direction": "output"}, by=total_usage["output_tokens"])
    yield (
        "done",
        {
            "message_id": assistant_row["id"],
            "usage": total_usage,
            "stop_reason": "end_turn",
        },
    )
