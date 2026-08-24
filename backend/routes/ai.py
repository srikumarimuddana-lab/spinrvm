"""AI assistant endpoints.

POST /ai/chat                       — SSE stream (default) or single JSON reply
POST /ai/public-chat                — ANONYMOUS, for the public spinr.ca site
GET  /ai/config                     — feature flag + disclaimer (gates UI entry points)
GET  /ai/conversations              — owner-scoped list
GET  /ai/conversations/{id}/messages
DELETE /ai/conversations/{id}      — PIPEDA right-to-delete

SSE is hand-rolled over StreamingResponse (sse-starlette is not in the
lockfile): `event: <name>\\ndata: <json>\\n\\n` frames with a `: ping`
comment every 15s so Fly/Railway proxies don't idle the connection.
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

try:
    from ai import conversations
    from ai.orchestrator import run_chat_turn
    from ai.public_assistant import PublicAssistantError, run_public_turn
    from dependencies import get_current_user
    from settings_loader import get_app_settings
    from utils.rate_limiter import ai_chat_limit, ai_public_chat_limit
except ImportError:
    from ..ai import conversations  # type: ignore
    from ..ai.orchestrator import run_chat_turn  # type: ignore
    from ..ai.public_assistant import PublicAssistantError, run_public_turn  # type: ignore
    from ..dependencies import get_current_user  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
    from ..utils.rate_limiter import ai_chat_limit, ai_public_chat_limit  # type: ignore

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/ai", tags=["AI Assistant"])

_PING_INTERVAL_SECONDS = 15

_ERROR_STATUS = {
    "ai_disabled": 503,
    "daily_cap": 429,
    "not_found": 404,
    "ai_misconfigured": 503,
    "provider_error": 502,
}

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # disable proxy buffering (Fly/Railway/nginx)
}


class AiChatLocation(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class AiChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    conversation_id: Optional[str] = Field(None, max_length=64)
    stream: bool = True
    # Rider's current device location, sent opportunistically by the app so
    # booking tools can bias place search / resolve "my location" pickups.
    # Ephemeral: passed to tools for this turn only — never logged, never
    # persisted (PIPEDA: raw GPS must not reach logs or storage).
    location: Optional[AiChatLocation] = None
    # Persona the calling surface wants. Dual-role (rider+driver) accounts
    # would otherwise always infer "driver", which strips the booking tools
    # from the rider app's main-screen assistant. "rider" is open to every
    # account (anyone can ride); "driver" is gated on the user row.
    audience: Optional[str] = Field(None, pattern="^(rider|driver)$")
    # UI features this client build can render (e.g. "map_pin" → the chat's
    # Drop-a-pin card). Tools that emit client actions check these so an
    # older installed app is never told a button is visible that its build
    # cannot draw. Absent list = no optional capabilities (old clients).
    capabilities: Optional[list[str]] = Field(None, max_length=20)


class PublicChatMessage(BaseModel):
    """One prior turn, replayed by the browser. Only plain user/assistant text
    is accepted — public_assistant._clean_history drops anything else, so a
    caller cannot inject a fabricated tool result the model would trust."""

    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=2000)


class PublicChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    # The website is stateless from the backend's point of view: nothing is
    # persisted for an anonymous visitor (PIPEDA data minimization), so the
    # browser replays the transcript. Bounded here AND again in
    # public_assistant.MAX_HISTORY_MESSAGES.
    history: Optional[list[PublicChatMessage]] = Field(None, max_length=8)
    # Which FAQ rows to search — the site knows whether the visitor is reading
    # rider or driver pages. It selects content only; it grants nothing, and it
    # is NOT the tool audience (that is always "web").
    visitor_type: Optional[str] = Field(None, pattern="^(rider|driver)$")


def _audience_for(user: dict) -> str:
    # SupportScreen is shared by both apps; the user row decides the tool set.
    return "driver" if user.get("is_driver") else "rider"


def _resolve_audience(requested: Optional[str], user: dict) -> str:
    if requested == "driver" and not user.get("is_driver"):
        raise HTTPException(status_code=403, detail="Driver assistant is only available to driver accounts")
    return requested or _audience_for(user)


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def _sse_with_pings(frames):
    """Wrap the orchestrator frame generator, interleaving keep-alive pings
    whenever no frame arrives within the ping interval."""
    iterator = frames.__aiter__()
    while True:
        next_frame = asyncio.ensure_future(iterator.__anext__())
        while True:
            done, _ = await asyncio.wait({next_frame}, timeout=_PING_INTERVAL_SECONDS)
            if done:
                break
            yield ": ping\n\n"
        try:
            name, payload = next_frame.result()
        except StopAsyncIteration:
            return
        yield _sse(name, payload)


@api_router.get("/config")
async def ai_config(current_user: dict = Depends(get_current_user)):
    settings = await get_app_settings()
    enabled = bool(settings.get("ai_assistant_enabled"))
    return {
        "enabled": enabled,
        # How the apps should present AI entry points while disabled:
        # "coming_soon" (keep icon, show placeholder) | "hidden" (remove icon).
        "mode": "enabled" if enabled else (settings.get("ai_disabled_mode") or "coming_soon"),
        "disclaimer": settings.get("ai_disclaimer", ""),
    }


@api_router.post("/chat")
@ai_chat_limit
async def ai_chat(
    body: AiChatRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    frames = run_chat_turn(
        user=current_user,
        conversation_id=body.conversation_id,
        user_message=body.message,
        audience=_resolve_audience(body.audience, current_user),
        client_location=body.location.model_dump() if body.location else None,
        client_capabilities=[c[:32] for c in (body.capabilities or [])],
    )

    if body.stream:
        return StreamingResponse(_sse_with_pings(frames), media_type="text/event-stream", headers=_SSE_HEADERS)

    # Non-streaming (SupportScreen tab): drain the frames into one reply.
    reply_parts: list = []
    actions: list = []
    meta: dict = {}
    done: dict = {}
    async for name, payload in frames:
        if name == "token":
            reply_parts.append(payload.get("text", ""))
        elif name == "action":
            actions.append(payload)
        elif name == "meta":
            meta = payload
        elif name == "done":
            done = payload
        elif name == "error":
            raise HTTPException(
                status_code=_ERROR_STATUS.get(payload.get("code"), 502),
                detail={"code": payload.get("code"), "message": payload.get("message")},
            )
    return {
        "conversation_id": meta.get("conversation_id"),
        "message_id": done.get("message_id"),
        "reply": "".join(reply_parts),
        "actions": actions,
    }


@api_router.post("/public-chat")
@ai_public_chat_limit
async def ai_public_chat(body: PublicChatRequest, request: Request):
    """Anonymous assistant for the public spinr.ca website.

    NO AUTHENTICATION BY DESIGN — the caller is a website visitor, not an
    account. What keeps that safe is not this handler but the tool registry:
    the turn runs at audience "web", which resolves to exactly two read-only
    tools (search_faqs, get_company_info). Every account, ride and booking
    tool is rider/driver-only and execute_tool re-checks the audience before
    dispatching, so there is no user data in reach — and no user to scope it
    to, since the synthetic caller carries no id.

    Non-streaming: the website widget renders whole answers, so this returns
    one JSON body rather than the SSE frames the app clients consume.

    Gated by BOTH ai_assistant_enabled and ai_public_chat_enabled, and rate
    limited per IP (ai_public_chat_limit).
    """
    try:
        result = await run_public_turn(
            message=body.message,
            history=[m.model_dump() for m in body.history] if body.history else None,
            visitor_type=body.visitor_type or "rider",
        )
    except PublicAssistantError as exc:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.code, 502),
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    # provider/model are echoed for the website's own logging; usage lets it
    # track spend. No conversation id — nothing was persisted to refer back to.
    return result


@api_router.get("/conversations")
async def list_ai_conversations(current_user: dict = Depends(get_current_user)):
    return {"conversations": await conversations.list_conversations(current_user["id"])}


@api_router.get("/conversations/{conversation_id}/messages")
async def get_ai_conversation_messages(conversation_id: str, current_user: dict = Depends(get_current_user)):
    messages = await conversations.get_messages(conversation_id, current_user["id"])
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"messages": messages}


@api_router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_ai_conversation(conversation_id: str, current_user: dict = Depends(get_current_user)):
    deleted = await conversations.delete_conversation(conversation_id, current_user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return None
