"""
CASL-compliant marketing SMS sender.

A marketing SMS is a commercial electronic message under CASL — same rules as
email: express consent before sending, identification of the sender, and a
working unsubscribe. So this wraps sms_service.send_sms with:

  1. EXPRESS-CONSENT GATE — services.marketing_consent.is_eligible("sms", …)
     must pass (opted in AND not on the SMS marketing suppression list).
  2. UNSUBSCRIBE INSTRUCTION — "Reply STOP to unsubscribe" appended to every
     message; the inbound Twilio STOP webhook records the opt-out.
  3. SENDER IDENTITY — the Twilio from-number is the registered sender.

PIPEDA: never logs the phone number — callers pass a PII-safe log_id.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from ..services import marketing_consent
    from ..sms_service import send_sms
except ImportError:  # pragma: no cover - direct module imports in tests
    from services import marketing_consent  # type: ignore
    from sms_service import send_sms  # type: ignore

_STOP_FOOTER = "\nReply STOP to unsubscribe."


async def send_marketing_sms(
    *,
    to: str,
    user_id: str,
    message: str,
    log_id: str = "-",
) -> bool:
    """Send one marketing SMS iff the recipient has express SMS consent and is
    not suppressed. Appends the STOP opt-out instruction. Returns True only when
    the provider accepted the message."""
    if not await marketing_consent.is_eligible("sms", user_id=user_id, target=to):
        logger.info("[MARKETING] sms skipped (no consent / suppressed) log_id=%s", log_id)
        return False

    try:
        from ..settings_loader import get_app_settings
    except ImportError:  # pragma: no cover
        from settings_loader import get_app_settings  # type: ignore
    settings = await get_app_settings()

    body = message.rstrip() + _STOP_FOOTER
    result = await send_sms(
        to,
        body,
        twilio_sid=(settings.get("twilio_account_sid") or ""),
        twilio_token=(settings.get("twilio_auth_token") or ""),
        twilio_from=(settings.get("twilio_from_number") or ""),
    )
    ok = bool(result.get("success"))
    if not ok:
        logger.error("[MARKETING] sms send failed log_id=%s provider=%s", log_id, result.get("provider"))
    return ok
