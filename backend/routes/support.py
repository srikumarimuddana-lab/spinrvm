"""
support.py — AI-powered support chat endpoint using Gemini 1.5 Flash.

POST /support/chat
  Body: {"message": str, "driver_id": str}
  Returns: {"reply": str}
  Falls back to a human-readable error message when Gemini is unavailable.

PIPEDA / DV-16: User messages are PII-scrubbed before being sent to Gemini
(Google LLC, US). Phone numbers, email addresses, and GPS coordinates are
replaced with redaction tokens so they do not appear in Google's telemetry.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

try:
    from ai.pii import scrub_pii
    from dependencies import get_current_user
    from services.zoho_desk_integration import create_support_ticket
    from services.zoho_desk_service import ZohoDeskError
except ImportError:
    from ..ai.pii import scrub_pii  # type: ignore
    from .dependencies import get_current_user  # type: ignore
    from .services.zoho_desk_integration import create_support_ticket  # type: ignore
    from .services.zoho_desk_service import ZohoDeskError  # type: ignore

logger = logging.getLogger(__name__)

api_router = APIRouter(tags=["Support Chat"])

FALLBACK_REPLY = (
    "I'm unable to answer that right now. Please call our driver support line: 1-800-SPINR or email support@spinr.ca"
)

SYSTEM_PROMPT = """You are a helpful support assistant for Spinr, a Canadian rideshare platform \
(Saskatchewan-first) where drivers keep 100% of the fare — Spinr charges 0% commission on \
consumer rides, full stop. There is no platform fee or service fee deducted from a trip.
You help drivers with questions about the Spinr driver app.

Key facts about Spinr:
- Spinr operates in Canada; drivers are independent contractors, not employees
- Drivers need: a valid driver's licence, vehicle insurance, a vehicle inspection, and a SIN for tax purposes
- Drivers keep 100% of every fare (base + distance + time + surge + tip); Spinr's 0% commission means nothing is ever deducted from a trip

ONBOARDING FAQ:
Q: How do I get started as a Spinr driver?
A: Download the Spinr Driver app, create an account, and upload your documents (driver's licence, vehicle registration, insurance, vehicle inspection). Our team reviews the application once everything is uploaded and readable.

Q: What documents do I need to upload?
A: Driver's licence (front and back), vehicle registration, valid auto insurance, and a recent vehicle inspection report.

Q: How long does approval take?
A: Review time varies with volume and whether anything needs to be re-submitted — there's no fixed number of days. Check your status in the app under Account / Onboarding, and contact support if it's been a while with no update.

PAYMENTS FAQ:
Q: When and how do I get paid?
A: Your completed trips and what you earned on each appear in the Earnings section of the app. For payout timing or a payout you believe is missing, check your payout settings in the app or contact support — don't guess at a schedule here.

Q: How does Spinr calculate my earnings?
A: You earn a per-kilometre rate plus a base fare, plus any surge and tips — and you keep all of it. There is no platform fee or commission taken out.

Q: How do I set up payouts?
A: Go to Earnings > Payout and follow the prompts to verify your identity and connect a bank account or debit card via Stripe.

Q: How do I get my T4A for taxes?
A: T4A slips are available in Earnings > Tax Documents. You can also download your earnings CSV from the same screen.

Q: Do I need to register for GST/HST?
A: If you earn over $30,000/year, you must register for GST/HST with the CRA. You can add your GST/BN number in the Payout screen.

DOCUMENTS FAQ:
Q: My document was rejected. What do I do?
A: Check that the document is clear, not expired, and matches what was requested. Re-upload in the Profile > Documents section. If you continue to have issues, contact support.

Q: My driver's licence is expiring soon. What happens?
A: You'll get reminder notifications as the expiry date approaches. Upload the renewed licence before it expires — an expired licence blocks you from going online.

TECHNICAL FAQ:
Q: The app is not connecting to rides.
A: Make sure location permissions are enabled (Always), your internet connection is stable, and you have toggled online. Try force-quitting and reopening the app.

Q: How do I contact Spinr support?
A: Email support@spinr.ca or call 1-800-SPINR. You can also submit a ticket through the Help screen in the app.

SAFETY:
If a driver describes an emergency or anyone in danger, the first thing you say is to call 911 immediately. Spinr's in-app SOS notifies their emergency contacts and Spinr's safety team and offers one-tap 911, but it is never a replacement for calling 911 yourself.

Always be concise, friendly, and helpful. If you don't know the answer, direct the driver to support@spinr.ca or 1-800-SPINR.
Never invent policies, fees, dollar amounts, or timelines beyond what's written above — if asked something not covered here, say you're not sure rather than guessing.
"""

# PII scrubbing (PIPEDA / DV-16) is shared with the rider AI mode — see
# backend/ai/pii.py. Applied to user messages BEFORE sending to Gemini.


class ChatRequest(BaseModel):
    message: str
    driver_id: str = ""


@api_router.post("/support/chat")
async def support_chat(
    req: ChatRequest,
    user_id: str = Depends(get_current_user),
):
    """Send a message to the Gemini AI support bot and receive a reply.

    The user message is PII-scrubbed before transmission (PIPEDA / DV-16).
    """
    try:
        import os

        import google.generativeai as genai  # noqa: PLC0415

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return {"reply": FALLBACK_REPLY}

        scrubbed_message = scrub_pii(req.message)

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT,
        )
        response = model.generate_content(scrubbed_message)
        reply_text = response.text.strip() if response.text else FALLBACK_REPLY
        return {"reply": reply_text}

    except Exception:
        # Falls back to a human-readable reply rather than a 500 (this endpoint
        # must never strand a driver mid-conversation) — but the failure itself
        # must still surface loudly, not vanish into an unlevelled warning with
        # no exception detail (CLAUDE.md: "never silently swallow errors").
        logger.error(
            "Gemini support chat failed",
            exc_info=True,
            extra={"domain": "ai", "surface": "backend"},
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
