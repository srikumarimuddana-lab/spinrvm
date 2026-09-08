"""
support.py — legacy support-chat compatibility shim + Zoho escalation.

POST /support/chat
  Body: {"message": str, "driver_id": str}
  Returns: {"reply": str}

F04 (2026-09-08 AI security assessment): this route used to call Gemini
directly. It was mounted, authenticated, and completely outside every AI
control added since: it never consulted ``ai_assistant_enabled`` (the "stop
sending user data to the third-party LLM provider" incident lever), never went
through the provider factory, never counted against the per-user AI daily
quota, had no chat-length bound, and called the synchronous
``generate_content`` inside an async route — so a slow provider blocked that
worker's event loop.

It now delegates to the same central engine as ``/api/v1/ai/chat``
(``ai.orchestrator.run_chat_turn``), which applies all of the above. The route
is kept rather than deleted purely for compatibility: no client in this repo
calls it (the shared help-centre UI uses ``/ai/chat`` —
``shared/components/SupportScreen.tsx``), but an older installed build in the
field might, and a 404 there would strand a driver mid-conversation. New
clients must use ``/api/v1/ai/chat``.

PII scrubbing, conversation persistence and tool authorization now all happen
inside the central engine, on the same terms as every other AI surface.
"""

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

try:
    from ai.orchestrator import run_chat_turn
    from dependencies import get_current_user, get_current_user_active_session
    from services.zoho_desk_integration import create_support_ticket
    from services.zoho_desk_service import ZohoDeskError
    from utils.rate_limiter import ai_chat_limit
except ImportError:
    from ..ai.orchestrator import run_chat_turn  # type: ignore
    from ..utils.rate_limiter import ai_chat_limit  # type: ignore
    from .dependencies import (  # type: ignore
        get_current_user,
        get_current_user_active_session,
    )
    from .services.zoho_desk_integration import create_support_ticket  # type: ignore
    from .services.zoho_desk_service import ZohoDeskError  # type: ignore

logger = logging.getLogger(__name__)

api_router = APIRouter(tags=["Support Chat"])

FALLBACK_REPLY = (
    "I'm unable to answer that right now. Please call our driver support line: 1-800-SPINR or email support@spinr.ca"
)

# SYSTEM_PROMPT was removed with the direct Gemini call (F04). The persona,
# the 0%-commission guarantee and the 911 redirect it carried now come from
# ai/prompts.py, which every AI surface shares — a second copy here was how
# this endpoint's prompt came to drift and state a fabricated payout timeline
# and a platform fee that does not exist. Its content assertions moved to
# tests/test_ai_prompts_policy.py, pointed at the central prompts.


class ChatRequest(BaseModel):
    # Bounded like AiChatRequest.message (routes/ai.py). The legacy field had no
    # length limit at all, so an arbitrarily long body went straight to the
    # provider — a cost and prompt-stuffing surface the central route never had.
    message: str = Field(..., min_length=1, max_length=1000)
    # Accepted and ignored, as it always was. The turn is scoped to the
    # authenticated caller; a client-supplied id has never selected an identity
    # here and must not start doing so.
    driver_id: str = ""


@api_router.post("/support/chat")
@ai_chat_limit
async def support_chat(
    req: ChatRequest,
    request: Request,
    # F02: same session-revocation gate as /api/v1/ai/chat. This is an AI turn
    # now, so it must not outlive a signed-out session either.
    current_user: dict = Depends(get_current_user_active_session),
):
    """Legacy support-chat endpoint. Delegates to the central AI engine.

    Deprecated — new clients use ``POST /api/v1/ai/chat``. Kept only so an
    older installed build does not 404. See the module docstring for the F04
    finding this rewrite closes.

    Returns the legacy ``{"reply": str}`` shape unchanged, so an old client
    parses the response exactly as before.
    """
    # Audience mirrors routes/ai.py::_audience_for — the user row decides the
    # tool set; the request body never does.
    audience = "driver" if current_user.get("is_driver") else "rider"

    reply_parts: list[str] = []
    try:
        async for name, payload in run_chat_turn(
            user=current_user,
            conversation_id=None,
            user_message=req.message,
            audience=audience,
        ):
            if name == "token":
                reply_parts.append(payload.get("text", ""))
            elif name == "error":
                # The engine's own refusal (AI disabled, quota exceeded,
                # provider misconfigured). Log it and fall back rather than
                # 500 — an old client has no handler for a structured error
                # body, and this endpoint must never strand a driver
                # mid-conversation.
                logger.error(
                    "legacy support chat refused by the AI engine",
                    extra={
                        "domain": "ai",
                        "surface": "backend",
                        "code": payload.get("code"),
                    },
                )
                return {"reply": FALLBACK_REPLY}
    except Exception:
        # The failure still surfaces loudly (CLAUDE.md: never swallow an error
        # into an unlevelled warning), but the response stays human-readable.
        logger.error(
            "legacy support chat failed",
            exc_info=True,
            extra={"domain": "ai", "surface": "backend"},
        )
        return {"reply": FALLBACK_REPLY}

    reply = "".join(reply_parts).strip()
    return {"reply": reply or FALLBACK_REPLY}


class EscalateRequest(BaseModel):
    message: str
    transcript: str = ""


@api_router.post("/support/escalate")
async def support_escalate(
    req: EscalateRequest,
    current_user: dict = Depends(get_current_user),
):
    """Escalate a support chat to a human by opening a Zoho Desk ticket.

    Returns the ticket number on success; on a Zoho outage / disabled
    integration it falls back to the support contact line so the user is never
    left without a path to help.
    """
    try:
        result = await create_support_ticket(user=current_user, message=req.message, transcript=req.transcript or None)
    except ZohoDeskError:
        return {"success": False, "reply": FALLBACK_REPLY}
    return {
        "success": True,
        "ticket_number": result.get("ticketNumber"),
        "reply": "Your request has been escalated to our support team. We'll follow up by email shortly.",
    }
