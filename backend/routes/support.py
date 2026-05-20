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

import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel

try:
    from dependencies import get_current_user
except ImportError:
    from .dependencies import get_current_user  # type: ignore

api_router = APIRouter(tags=["Support Chat"])

FALLBACK_REPLY = "I'm unable to answer that right now. Please call our driver support line: 1-800-SPINR or email support@spinr.ca"

SYSTEM_PROMPT = """You are a helpful support assistant for Spinr, a Canadian rideshare platform.
You help drivers with questions about the Spinr driver app.

Key facts about Spinr:
- Spinr operates in Canada; drivers are independent contractors
- Drivers need: valid driver's licence, vehicle insurance, vehicle inspection, SIN for tax purposes

ONBOARDING FAQ:
Q: How do I get started as a Spinr driver?
A: Download the Spinr Driver app, create an account, upload your documents (driver's licence, vehicle registration, insurance, vehicle inspection), and wait for approval (usually 2–3 business days).

Q: What documents do I need to upload?
A: Driver's licence (front and back), vehicle registration, valid auto insurance, and a recent vehicle inspection report.

Q: How long does approval take?
A: Typically 2–3 business days. You'll receive a push notification once approved.

PAYMENTS FAQ:
Q: When do I get paid?
A: You can request a payout from the Earnings > Payout screen at any time. Payouts are processed via Stripe and arrive in your bank account within 2–3 business days. Minimum payout is $10.

Q: How does Spinr calculate my earnings?
A: You earn a per-kilometre rate plus a base fare, minus the platform service fee. Tips are passed to you in full.

Q: What is the platform fee?
A: The platform fee varies by market and is shown on your earnings dashboard.

Q: How do I set up payouts?
A: Go to Earnings > Payout and tap "Set Up Payouts with Stripe". You'll need to verify your identity and add a bank account or debit card.

Q: How do I get my T4A for taxes?
A: T4A slips are available in Earnings > Tax Documents. You can also download your earnings CSV from the same screen.

Q: Do I need to register for GST/HST?
A: If you earn over $30,000/year, you must register for GST/HST with the CRA. You can add your GST/BN number in the Payout screen.

DOCUMENTS FAQ:
Q: My document was rejected. What do I do?
A: Check that the document is clear, not expired, and matches what was requested. Re-upload in the Profile > Documents section. If you continue to have issues, contact support.

Q: My driver's licence is expiring soon. What happens?
A: You'll receive a reminder notification 30 days before expiry. Upload the renewed licence before it expires to avoid deactivation.

TECHNICAL FAQ:
Q: The app is not connecting to rides.
A: Make sure location permissions are enabled (Always), your internet connection is stable, and you have toggled online. Try force-quitting and reopening the app.

Q: How do I contact Spinr support?
A: Email support@spinr.ca or call 1-800-SPINR. You can also submit a ticket through the Help screen in the app.

Always be concise, friendly, and helpful. If you don't know the answer, direct the driver to support@spinr.ca or 1-800-SPINR.
Do not invent policies or fees. Only reference the information above.
"""

# ── PII scrubbing patterns (PIPEDA / DV-16) ──────────────────────────────────
# Applied to user messages BEFORE sending to Gemini (Google LLC, US).
# Names cannot be scrubbed reliably with regex; mitigate via data-minimization
# principle: the system prompt never asks for names, and we strip the patterns
# below which cover the highest-risk identifiers.
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # North American phone numbers (+1 optional, various separators)
    (re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}"), "[PHONE]"),
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
    # GPS coordinates  lat,lng or lat/lng (±90/±180 range)
    (re.compile(r"-?\d{1,2}\.\d{4,},\s*-?\d{1,3}\.\d{4,}"), "[COORDS]"),
    # Canadian postal codes (A1A 1A1 or A1A1A1)
    (re.compile(r"\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b"), "[POSTAL]"),
]


def _scrub_pii(text: str) -> str:
    for pattern, token in _PII_PATTERNS:
        text = pattern.sub(token, text)
    return text


# ─────────────────────────────────────────────────────────────────────────────


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

        scrubbed_message = _scrub_pii(req.message)

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT,
        )
        response = model.generate_content(scrubbed_message)
        reply_text = response.text.strip() if response.text else FALLBACK_REPLY
        return {"reply": reply_text}

    except Exception as exc:
        import logging

        logging.warning("Gemini support chat failed: %s", exc)
        return {"reply": FALLBACK_REPLY}
