"""One-time opt-out notice sent to a newly-added SOS emergency contact
(PIA finding R-002, subtask 3 of 4).

A rider's emergency contact is a third party who never consented to being
texted. `services/sos_contact_consent.py` (subtask 1) provides the STOP
opt-out storage this notice advertises; this module sends the notice itself,
once, when a contact is added.

Fully best-effort: never raises, never blocks or fails the contact-add
request it's called from. Mirrors routes/rides/safety.py's SOS SMS pattern —
same `send_sms` helper, same twilio_sid/twilio_token/twilio_from sourcing
from app_settings, same "log only the exception type, never PII" discipline.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from ..services import sos_contact_consent  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
    from ..sms_service import send_sms  # type: ignore
except ImportError:  # pragma: no cover - direct module imports in tests
    from services import sos_contact_consent  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from sms_service import send_sms  # type: ignore


async def send_opt_out_notice(phone: str, rider_first_name: str) -> bool:
    """Best-effort one-time SMS telling `phone` they were added as someone's
    Spinr emergency contact, with a STOP opt-out.

    Skips sending (returns False) if this phone is already suppressed — no
    point notifying someone who already opted out of these texts. Never
    raises: any failure is logged and swallowed so a bad send can't block or
    fail the contact-add request that triggered it.
    """
    try:
        if await sos_contact_consent.is_suppressed(phone):
            return False

        first_name = (rider_first_name or "").strip() or "A Spinr user"
        body = (
            f"{first_name} added you as their emergency contact on Spinr. "
            "If they trigger an SOS alert, you may receive a safety text. Reply STOP to opt out."
        )

        sms_settings = await get_app_settings()
        result = await send_sms(
            phone,
            body,
            twilio_sid=(sms_settings.get("twilio_account_sid", "") if sms_settings else ""),
            twilio_token=(sms_settings.get("twilio_auth_token", "") if sms_settings else ""),
            twilio_from=(sms_settings.get("twilio_from_number", "") if sms_settings else ""),
        )
        if result.get("success"):
            return True
        # send_sms guarantees 'error' is a PII-free "type code=N status=N"
        # string (never str(exception)) — safe to log directly.
        logger.error(f"SOS contact opt-out notice failed to send: {result.get('error')}")
        return False
    except Exception:
        # PIPEDA: never log the phone number or raw exception text (it can
        # embed the destination number) — exc_info carries the traceback for
        # us without putting PII in the message string itself.
        logger.error("SOS contact opt-out notice failed to send", exc_info=True)
        return False
