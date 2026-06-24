"""
marketing.py — public, unauthenticated marketing endpoints.

Currently the CASL unsubscribe handler. The link in every marketing message and
the List-Unsubscribe header point here. It carries no JWT (the recipient clicks
from their mail client), so authorisation is the signed token
(utils/unsubscribe_token) — possession of a valid token IS the proof the
recipient controls the mailbox/number.

Two methods on the same path:
  • POST /marketing/unsubscribe  — RFC 8058 one-click. Mail clients fire this
    automatically from the List-Unsubscribe-Post header; it MUST act without a
    confirmation step and return 200.
  • GET  /marketing/unsubscribe  — the human-visible link; processes the opt-out
    and returns a small confirmation page.

Unsubscribing: flips the channel opt-in to false (with an audit event) AND adds
the contact to the marketing suppression list, so even a future re-opt-in race
can't resurrect a complaint source. Idempotent and existence-agnostic — the
response never reveals whether the user/address exists.
"""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
    from ..services import marketing_consent
    from ..utils.rate_limiter import default_limiter as limiter
    from ..utils.unsubscribe_token import UnsubscribeTokenError, verify_unsubscribe_token
except ImportError:  # pragma: no cover - direct module imports in tests
    import db_supabase  # type: ignore
    from dependencies import get_current_user  # type: ignore
    from services import marketing_consent  # type: ignore
    from utils.rate_limiter import default_limiter as limiter  # type: ignore
    from utils.unsubscribe_token import UnsubscribeTokenError, verify_unsubscribe_token  # type: ignore

logger = logging.getLogger(__name__)

# Version of the marketing-consent language in effect. Bump when the consent
# wording materially changes (PIPEDA: material changes require re-consent).
CONSENT_VERSION = "1"

api_router = APIRouter(prefix="/marketing", tags=["Marketing"])

_CONFIRM_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Unsubscribed</title></head>"
    "<body style='font-family:sans-serif;max-width:480px;margin:64px auto;text-align:center'>"
    "<h2>You're unsubscribed</h2>"
    "<p>You will no longer receive marketing {channel} from Spinr. "
    "Service messages (receipts, ride updates, safety) are unaffected.</p>"
    "</body></html>"
)
_ERROR_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Link invalid</title></head>"
    "<body style='font-family:sans-serif;max-width:480px;margin:64px auto;text-align:center'>"
    "<h2>This unsubscribe link is invalid</h2>"
    "<p>Please use the unsubscribe link from a recent message.</p>"
    "</body></html>"
)


async def _process_unsubscribe(token: str) -> str:
    """Opt the token's user out of its channel and suppress the contact.

    Returns the channel on success. Raises UnsubscribeTokenError on a bad token.
    Idempotent: re-running is a no-op (consent already false, suppression unique).
    """
    payload = verify_unsubscribe_token(token)
    user_id, channel = payload["user_id"], payload["channel"]

    # Resolve the contact value to suppress (email/phone) — best effort.
    target = None
    try:
        user = await db_supabase.find_one("users", {"id": user_id})
        if user:
            target = user.get("email") if channel == "email" else user.get("phone")
    except Exception:
        logger.error("[MARKETING] unsubscribe user lookup failed channel=%s", channel, exc_info=True)

    await marketing_consent.set_consent(user_id, channel, False, source="unsubscribe_link")
    if target:
        await marketing_consent.add_marketing_suppression(
            channel, target, reason="unsubscribe", source="unsubscribe_link", user_id=user_id
        )
    logger.info("[MARKETING] unsubscribe processed channel=%s user_id=%s", channel, user_id)
    return channel


@api_router.post("/unsubscribe")
@limiter.limit("60/minute")
async def unsubscribe_one_click(request: Request, token: str = Query(default="")):
    """RFC 8058 one-click unsubscribe. Always acts; returns 200 on success."""
    try:
        await _process_unsubscribe(token)
    except UnsubscribeTokenError:
        return JSONResponse({"error": "invalid token"}, status_code=400)
    return JSONResponse({"status": "unsubscribed"}, status_code=200)


@api_router.get("/unsubscribe", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def unsubscribe_page(request: Request, token: str = Query(default="")):
    """Human-visible unsubscribe link: process the opt-out and confirm."""
    try:
        channel = await _process_unsubscribe(token)
    except UnsubscribeTokenError:
        return HTMLResponse(_ERROR_HTML, status_code=400)
    label = "emails" if channel == "email" else "messages"
    return HTMLResponse(_CONFIRM_HTML.format(channel=label), status_code=200)


# ── Authenticated consent preferences (rider/driver apps) ───────────────────


class MarketingPreferencesUpdate(BaseModel):
    """Partial update — only provided channels change. Source attributes the
    consent to the originating app for the CASL audit trail."""

    email_opt_in: Optional[bool] = None
    sms_opt_in: Optional[bool] = None
    push_opt_in: Optional[bool] = None
    source: Literal["rider_app", "driver_app"] = "rider_app"


@api_router.get("/preferences")
async def get_marketing_preferences(current_user: dict = Depends(get_current_user)):
    """Current user's marketing opt-in state per channel (default all false)."""
    prefs = await marketing_consent.get_preferences(current_user["id"])
    return {
        "email_opt_in": bool(prefs.get("email_opt_in")),
        "sms_opt_in": bool(prefs.get("sms_opt_in")),
        "push_opt_in": bool(prefs.get("push_opt_in")),
        "consent_version": prefs.get("consent_version"),
    }


@api_router.put("/preferences")
async def update_marketing_preferences(
    body: MarketingPreferencesUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Set the current user's marketing consent. Each provided channel is
    written with an audit event (CASL proof-of-consent). On opt-out the contact
    is also added to the marketing suppression list so an in-flight broadcast
    can't still reach it."""
    user_id = current_user["id"]
    changes = {"email": body.email_opt_in, "sms": body.sms_opt_in, "push": body.push_opt_in}
    for channel, value in changes.items():
        if value is None:
            continue
        await marketing_consent.set_consent(
            user_id, channel, value, source=body.source, consent_version=CONSENT_VERSION if value else None
        )
        if not value and channel in ("email", "sms"):
            target = current_user.get("email") if channel == "email" else current_user.get("phone")
            if target:
                await marketing_consent.add_marketing_suppression(
                    channel, target, reason="unsubscribe", source="unsubscribe_link", user_id=user_id
                )
    return await get_marketing_preferences(current_user)
