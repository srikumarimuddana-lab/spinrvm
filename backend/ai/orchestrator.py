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
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

try:
    from . import conversations, response_cache
    from .guardrails import fallback_over_cap
    from .pii import ScrubPolicy, filter_tool_leakage, scrub_pii
    from .prompts import FARE_CHECK_BLOCK_HEADER, build_system_prompt
    from .providers import get_adapter
    from .providers.base import AIConfigError
    from .stream_filter import StreamingOutputFilter
    from .threat import record_security_event, scan_message
    from .tools import execute_tool, tool_defs_for
except ImportError:
    from ai import conversations, response_cache
    from ai.guardrails import fallback_over_cap
    from ai.pii import ScrubPolicy, filter_tool_leakage, scrub_pii
    from ai.prompts import FARE_CHECK_BLOCK_HEADER, build_system_prompt
    from ai.providers import get_adapter
    from ai.providers.base import AIConfigError
    from ai.stream_filter import StreamingOutputFilter
    from ai.threat import record_security_event, scan_message
    from ai.tools import execute_tool, tool_defs_for

try:
    from ..core.config import settings
    from ..settings_loader import get_app_settings
    from ..utils.metrics import inc as _metric_inc
    from ..utils.redis_client import (
        redis_delete,
        redis_eval,
        redis_expire,
        redis_get,
        redis_incr,
        redis_set_nx,
    )
except ImportError:
    from core.config import settings
    from settings_loader import get_app_settings
    from utils.metrics import inc as _metric_inc
    from utils.redis_client import (
        redis_delete,
        redis_eval,
        redis_expire,
        redis_get,
        redis_incr,
        redis_set_nx,
    )

logger = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "Something went wrong on our side — please try again in a moment."

# AI3: a single iteration's tool_calls come straight from the model — an
# adversarial or hallucinating turn could request an unbounded number, each
# potentially hitting a paid upstream (Maps, via tools_booking's find_place /
# get_fare_quote / propose_ride_booking). Cap fan-out per iteration; this is
# independent of TOOL_TIMEOUT_SECONDS (backend/ai/tools.py), which bounds a
# single call's wall-clock time, not how many run concurrently. A real
# booking turn (find_place pickup + find_place dropoff + get_fare_quote +
# propose_ride_booking) tops out around 4 calls, so 5 leaves headroom without
# raising the ceiling on paid-API fan-out.
MAX_TOOL_CALLS_PER_ITERATION = 5

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
    if pinned.get("no_drivers"):
        # Pinned by get_fare_quote's no-drivers branch: endpoints only, no
        # vehicle or total, so this block is a re-quote instruction and must
        # never read as a bookable trip (rule 6c's booking shortcut keys on
        # the "LAST QUOTE" wording, which this deliberately does not use).
        return "\n".join(
            [
                f"\n\n{FARE_CHECK_BLOCK_HEADER} (no drivers were available, so nothing was priced):",
                f"- pickup: {pinned.get('pickup_address') or 'unnamed'} [{pickup_lat},{pickup_lng}]",
                f"- dropoff: {pinned.get('dropoff_address') or 'unnamed'} [{dropoff_lat},{dropoff_lng}]",
                "If the rider says yes to re-checking or asks to try again, call get_fare_quote with exactly "
                "these coordinates and addresses — do NOT re-resolve them, and do NOT call "
                "propose_ride_booking from this block: there is no priced trip to book. This block replaced "
                "any earlier priced quote in this conversation — if the rider refers to an earlier trip, "
                "re-quote it with get_fare_quote before proposing.",
            ]
        )
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
    """Per-user daily message cap via Redis INCR. On a Redis error, falls
    back to a bounded, process-local cap (AI1b, GitHub #3742) rather than
    failing open — see ai/guardrails.py for why AI usage can't fail open the
    way the best-effort leader locks elsewhere do."""
    key = f"ai:daily:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    try:
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, 86400)
        return count > cap
    except Exception:
        logger.error(
            "ai daily-cap check failed — falling back to bounded process-local cap",
            exc_info=True,
            extra={"user_id": user_id},
        )
        return fallback_over_cap(user_id, cap)


_CONV_LOCK_TTL_SECONDS = 90  # generous ceiling for a full multi-iteration tool-calling turn

# AI17/F1: forcing StreamingOutputFilter's holdback past any real reply's
# length makes _release(final=False) always return "" (see its `stable =
# full[: max(0, len(full) - self._holdback)]`), so nothing leaves the
# filter until flush(). Used only when ai_stream_incremental_enabled is
# False — filtering itself is unaffected either way.
_DISABLE_INCREMENTAL_HOLDBACK = 1 << 30


# Release the conversation lock ONLY if we still own it (F09).
#
# The lock used to be released with an unconditional DELETE, which is unsafe
# whenever a turn outlives the TTL: turn A's release then deletes turn B's
# freshly-acquired lock, and turn C can start alongside B — the release makes
# the concurrency it is meant to prevent MORE likely, not less. Storing a
# per-turn token and deleting only on a match closes that.
#
# Compare-and-delete has to be atomic, hence Lua: a GET-then-DELETE can still
# delete B's lock if B acquires in the gap between the two calls.
def _redis_configured() -> bool:
    """Whether a real Redis is configured, as opposed to redis_client's
    in-process dict fallback. Decides which release path is sound — see
    _release_conversation_lock."""
    return bool(getattr(settings, "REDIS_URL", None))


_RELEASE_IF_OWNER_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


async def _release_conversation_lock(lock_key: str, token: str) -> None:
    """Best-effort ownership-checked release. Never raises.

    A failed release is not an error worth propagating: the lock carries a TTL,
    so the worst case is that the conversation stays locked for the remainder
    of it. Raising here would turn a Redis blip into a failed chat turn whose
    reply the rider has already seen streamed.
    """
    if _redis_configured():
        try:
            await redis_eval(_RELEASE_IF_OWNER_LUA, 1, lock_key, token)
        except Exception:
            # Includes RuntimeError. redis_eval re-raises RuntimeErrors coming
            # from a LIVE connection as well as its own "unconfigured" one
            # (see its `except RuntimeError: raise`), so catching RuntimeError
            # here to mean "no Lua available" would silently drop a real Redis
            # error onto the non-atomic path below — against a cluster-shared
            # key, which is exactly the clobber the token exists to prevent.
            # Configured Redis therefore gets the atomic path or nothing.
            logger.error(
                "ai conversation-lock release failed — lock will expire on its TTL",
                exc_info=True,
                extra={"lock_key": lock_key},
            )
        return

    # REDIS_URL unset — redis_client is a single in-process dict and there is
    # no Lua interpreter (see redis_eval's docstring). No other replica can
    # hold this key, so a non-atomic compare-and-delete is exact here.
    try:
        if await redis_get(lock_key) == token:
            await redis_delete(lock_key)
    except Exception:
        logger.error(
            "ai conversation-lock in-process release failed — lock will expire on its TTL",
            exc_info=True,
        )


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
    """Public entry point — adds a conversation-level lock around _run_chat_turn.

    AI10: two clients (or two tabs/devices) sending turns on the same
    conversation_id concurrently would otherwise interleave append_message
    writes and race history snapshots — each turn's LLM call is built from
    a load_history() read at its own start, so a second turn that starts
    before the first finishes doesn't see the first's messages yet. A new
    conversation (conversation_id is None) has no shared id for another
    request to race against, so it skips the lock entirely.
    """
    if not conversation_id:
        async for frame in _run_chat_turn(
            user=user,
            conversation_id=conversation_id,
            user_message=user_message,
            audience=audience,
            admin_actor_id=admin_actor_id,
            client_location=client_location,
            client_capabilities=client_capabilities,
        ):
            yield frame
        return

    lock_key = f"ai:conv_lock:{conversation_id}"
    # Unique per turn, so the release below can prove ownership (F09).
    lock_token = uuid.uuid4().hex
    # Distinct from `acquired`: on the fail-open path we proceed WITHOUT owning
    # the lock, and must not then release one we never took.
    lock_held = False
    try:
        acquired = await redis_set_nx(lock_key, lock_token, _CONV_LOCK_TTL_SECONDS)
        lock_held = acquired
    except Exception:
        # redis_set_nx now raises on a real (Redis-configured-but-
        # unavailable) error instead of silently falling back per-replica
        # (2026-08-11 P1 fix). Fails OPEN with a loud log, mirroring
        # _over_daily_cap's policy above — blocking every AI conversation on
        # a Redis blip is worse than occasionally racing two concurrent
        # turns on the same conversation (the AI10 bug this lock guards
        # against), which is a data-quality edge case, not a safety one.
        logger.error("ai conversation-lock acquisition failed — failing open", exc_info=True)
        acquired = True
    if not acquired:
        _metric_inc("spinr_ai_chat_turns_total", {"outcome": "conversation_busy"})
        yield (
            "error",
            {
                "code": "conversation_busy",
                "message": "This conversation has another reply in progress — please wait a moment and try again.",
            },
        )
        return
    try:
        async for frame in _run_chat_turn(
            user=user,
            conversation_id=conversation_id,
            user_message=user_message,
            audience=audience,
            admin_actor_id=admin_actor_id,
            client_location=client_location,
            client_capabilities=client_capabilities,
        ):
            yield frame
    finally:
        if lock_held:
            await _release_conversation_lock(lock_key, lock_token)


async def _run_chat_turn(
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

    # ScrubPolicy.AI_CHAT: chat messages may carry app-generated bracketed
    # [lat,lng] trip endpoints (quote-card taps, map-pin confirms) and the
    # postal code of a tapped address, both of which the model must see
    # verbatim (ADR 012). Only this path and tools.py's model-facing result
    # cap opt in — Sentry, support and /mcp scrubbing stay fully strict.
    scrubbed = scrub_pii(user_message, policy=ScrubPolicy.AI_CHAT)
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
    # AI17/F1: operational kill-switch for INCREMENTAL release only — an
    # explicit True default so a settings dict missing this key (a stale
    # cache entry, a test fixture) never accidentally disables streaming.
    # filter_tool_leakage/scrub_pii(AI_CHAT) run on the full reply either
    # way; see _DISABLE_INCREMENTAL_HOLDBACK above and migration 409.
    incremental_streaming_enabled = settings.get("ai_stream_incremental_enabled", True)
    all_text: List[str] = []
    emitted_text: List[str] = []
    used_tool_names: List[str] = []
    emitted_client_action = False
    cache_disqualified = False
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    try:
        for _iteration in range(max_iterations):
            turn_text: List[str] = []
            turn_end = None
            # F08 (AI security assessment (PR #5138)): provider text used to be
            # yielded raw and filtered only at the end, for the STORED copy —
            # so a clean ai_messages transcript did not prove the rider saw a
            # clean answer. Filter before delivery instead, through a buffer
            # that can see across chunk boundaries (a phone number arriving as
            # "306-" + "555-1234" matches neither half on its own).
            #
            # One filter per provider stream, not per turn: holding text back
            # across a tool call would stall the rider's visible reply behind
            # the tool's latency for no safety gain — a value cannot span two
            # separate provider responses.
            out_filter = (
                StreamingOutputFilter(policy=ScrubPolicy.AI_CHAT)
                if incremental_streaming_enabled
                else StreamingOutputFilter(policy=ScrubPolicy.AI_CHAT, holdback=_DISABLE_INCREMENTAL_HOLDBACK)
            )
            async for event in adapter.stream_turn(system=system, messages=messages, tools=tools):
                if event.type == "text" and event.text:
                    turn_text.append(event.text)
                    safe = out_filter.feed(event.text)
                    if safe:
                        yield "token", {"text": safe}
                elif event.type == "turn_end":
                    turn_end = event
            tail = out_filter.flush()
            if tail:
                yield "token", {"text": tail}
            if turn_end is None:  # adapter contract violation — surface loudly
                raise RuntimeError(f"adapter {adapter.provider} ended stream without turn_end")

            # What the rider actually saw, kept alongside the raw text. The
            # model context below must keep the RAW assistant text (redacting
            # the model's own prior turn would corrupt its context), while the
            # persisted/cached copy is derived from the emitted text.
            emitted_text.append(out_filter.emitted_text)
            all_text.extend(turn_text)
            for k in total_usage:
                total_usage[k] += int((turn_end.usage or {}).get(k, 0) or 0)

            tool_calls = turn_end.tool_calls or []
            if turn_end.stop_reason != "tool_use" or not tool_calls:
                break

            messages.append({"role": "assistant", "content": "".join(turn_text), "tool_calls": tool_calls})
            for tc in tool_calls:
                yield "tool", {"name": tc.name, "status": "start"}

            # AI3 cap: only the first MAX_TOOL_CALLS_PER_ITERATION calls run;
            # the rest are never dropped (CLAUDE.md "do not silently swallow
            # errors") — each gets a synthetic error result so the model sees
            # the refusal and the turn can still reach a final answer.
            executable_calls = tool_calls[:MAX_TOOL_CALLS_PER_ITERATION]
            excess_calls = tool_calls[MAX_TOOL_CALLS_PER_ITERATION:]
            if excess_calls:
                logger.warning(
                    "ai tool call budget exceeded: %d requested, %d executed, %d rejected",
                    len(tool_calls),
                    len(executable_calls),
                    len(excess_calls),
                    extra={
                        "user_id": user.get("id"),
                        "conversation_id": conversation["id"],
                        # tool names only — never arguments/results (PIPEDA)
                        "requested_tools": [tc.name for tc in tool_calls],
                    },
                )
                _metric_inc("spinr_ai_tool_calls_capped_total", by=len(excess_calls))

            executed_results = await asyncio.gather(
                *(execute_tool(tc.name, tc.arguments, user=tool_user, audience=audience) for tc in executable_calls)
            )

            def _capped_result() -> Tuple[Dict[str, Any], bool]:
                # A fresh dict per excess call — downstream mutates results
                # in place (e.g. result.pop("_client_action", ...)), so
                # aliasing one shared dict across multiple excess calls would
                # let one call's pop affect another's.
                return (
                    {
                        "error": (
                            f"tool call budget exceeded — only {MAX_TOOL_CALLS_PER_ITERATION} tool calls "
                            "are allowed per turn; this call was not executed, try again with fewer calls "
                            "this turn"
                        )
                    },
                    False,
                )

            results = list(executed_results) + [_capped_result() for _ in excess_calls]
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
    # What the rider actually saw this turn, after the streaming filter. Used
    # for the persisted/cached copy so the transcript matches the delivered
    # answer — before F08 the two could differ, and the stored one was the
    # only filtered one.
    delivered_text = "".join(emitted_text).strip()
    produced_real_text = bool(final_text)  # False → the generic fallback below
    if not final_text:
        final_text = "I couldn't finish that one — could you rephrase, or tap Contact Support?"
        delivered_text = final_text
        # Our own literal — nothing to filter.
        yield "token", {"text": final_text}

    # AI2 / PIPEDA (persisted copy) + AI13 (tool-name leakage). The user's
    # message is scrubbed before persistence above; the assistant's is
    # scrubbed here, because the model can echo tool-result data verbatim.
    #
    # This comment used to end "the raw text has already streamed to the
    # client this turn, so the rider still sees the real reply; only
    # stored/replayed copies change." That was a deliberate choice and F08
    # (AI security assessment (PR #5138)) found it wrong: a clean ai_messages
    # row proved nothing about what the rider actually received, and a
    # scripted provider reply containing a synthetic email reached the client
    # unchanged. The stream is filtered too now (StreamingOutputFilter above).
    #
    # Filtering here a second time is deliberate and idempotent — redaction
    # tokens never re-match a pattern. The persistence path must not depend on
    # the streaming path having been correct; if the stream filter ever
    # regresses, the stored copy still gets scrubbed rather than both failing
    # together.
    stored_text = filter_tool_leakage(scrub_pii(delivered_text, policy=ScrubPolicy.AI_CHAT))

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
            text=delivered_text,
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
