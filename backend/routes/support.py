"""
support.py — retired support-chat stub + Zoho escalation.

POST /support/chat
  Body: {"message": str, "driver_id": str}
  Returns: {"reply": str}  — always the support-line fallback.

F04 (AI security assessment, PR #5138): this route used to call Gemini
directly. It was mounted, authenticated, and completely outside every AI
control added since: it never consulted ``ai_assistant_enabled`` (the "stop
sending user data to the third-party LLM provider" incident lever), never went
through the provider factory, never counted against the per-user AI daily
quota, had no chat-length bound, and called the synchronous
``generate_content`` inside an async route — so a slow provider blocked that
worker's event loop.

It is now a stub that makes no provider call at all, so none of those controls
can be circumvented on this path by construction. See ``support_chat`` for why
a stub rather than a delegation to the central engine. POST /support/escalate
below is unaffected and still live.
"""

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

try:
    from ai.pii import scrub_pii
    from dependencies import get_current_user, get_current_user_active_session
    from services.zoho_desk_integration import create_support_ticket
    from services.zoho_desk_service import ZohoDeskError
    from utils.rate_limiter import ai_chat_limit
except ImportError:
    from ..utils.rate_limiter import ai_chat_limit  # type: ignore
    from .ai.pii import scrub_pii  # type: ignore
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
    current_user: dict = Depends(get_current_user_active_session),
):
    """Deprecated. Retired stub — returns the support-line fallback.

    New clients use ``POST /api/v1/ai/chat``. Nothing in this repo calls this
    route (the shared help-centre UI uses ``/ai/chat`` —
    ``shared/components/SupportScreen.tsx``); it is kept mounted only so an
    older installed build gets its familiar ``{"reply": ...}`` shape instead of
    a 404 mid-conversation.

    Why a stub rather than a delegation to the central engine: delegating was
    tried first and quietly WIDENED this endpoint. The pre-F04 route was a
    prompt-only FAQ bot with no tools, no stored conversation and no cache
    participation. Routing it through ``run_chat_turn`` handed a legacy client
    the full authenticated rider/driver tool set — including
    ``propose_ride_booking`` and ``escalate_to_support``, whose side effects
    (a real Zoho ticket) would fire while the resulting ``action`` frame was
    dropped, because this response shape has nowhere to put a card. It also
    passed ``conversation_id=None`` on every call, so every turn looked like a
    first turn (``prior_turns == 0``) and became eligible for the CROSS-USER
    FAQ response cache, letting a legacy-client answer be replayed to
    ``/api/v1/ai/chat`` riders.

    Returning the fallback closes the F04 bypass completely — there is no
    provider call on this path at all, so the global AI switch, the quota and
    the provider factory cannot be circumvented by definition — without
    granting the legacy surface anything it never had. ``FALLBACK_REPLY`` is
    the string this endpoint already returned whenever ``GEMINI_API_KEY`` was
    unset, so an old client is on a path it has always handled.
    """
    logger.info(
        "deprecated /support/chat called — returning the support-line fallback",
        extra={"domain": "ai", "surface": "backend", "user_id": current_user.get("id")},
    )
    return {"reply": FALLBACK_REPLY}


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

    ``message``/``transcript`` are scrubbed (ScrubPolicy.STRICT, same as
    ai/support_assistant.py's own use of scrub_pii on this same ticket data
    when drafting a reply) before Zoho Desk -- a third party -- ever sees
    them. A rider troubleshooting a declined card or typing their own phone
    number into the chat must not have it land in a support ticket verbatim.
    """
    scrubbed_message = scrub_pii(req.message)
    scrubbed_transcript = scrub_pii(req.transcript) if req.transcript else None
    try:
        result = await create_support_ticket(
            user=current_user, message=scrubbed_message, transcript=scrubbed_transcript
        )
    except ZohoDeskError:
        return {"success": False, "reply": FALLBACK_REPLY}
    return {
        "success": True,
        "ticket_number": result.get("ticketNumber"),
        "reply": "Your request has been escalated to our support team. We'll follow up by email shortly.",
    }
