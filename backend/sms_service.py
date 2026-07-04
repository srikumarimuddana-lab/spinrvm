"""
SMS Service for Spinr
Supports Twilio for production SMS delivery with console fallback for development.
Credentials are read from DB settings (passed in by caller), not env vars.
"""

import asyncio

from loguru import logger

try:
    from .utils.pii import redact_phone
except ImportError:
    from utils.pii import redact_phone


async def send_sms(
    to_phone: str, message: str, *, twilio_sid: str = "", twilio_token: str = "", twilio_from: str = ""
) -> dict:
    """
    Send an SMS message.

    When Twilio credentials are provided: sends real SMS via Twilio.
    Otherwise: logs to console and returns mock result.

    Returns:
        dict with 'success' (bool), 'provider' (str), and optionally 'sid' or 'error'.
    """
    masked = redact_phone(to_phone)
    if not all([twilio_sid, twilio_token, twilio_from]):
        # Development fallback — log to console (PII-safe: phone redacted, message dropped)
        logger.info(f"[DEV SMS] To: {masked} (Twilio not configured)")
        return {"success": True, "provider": "console", "message": "SMS logged to console (Twilio not configured)"}

    try:
        from twilio.rest import Client

        def _send() -> str:
            # Twilio's REST client is synchronous; run it in the default
            # threadpool so the HTTP round-trip doesn't block the event loop
            # (SOS fires several of these at once).
            client = Client(twilio_sid, twilio_token)
            return client.messages.create(body=message, from_=twilio_from, to=to_phone).sid

        sid = await asyncio.to_thread(_send)
        logger.info(f"SMS sent to {masked} via Twilio (SID: {sid})")
        return {"success": True, "provider": "twilio", "sid": sid}
    except Exception as e:
        # PIPEDA: never log or return str(e) — TwilioRestException text
        # embeds the destination number ("The 'To' number +1306... is not a
        # valid phone number"). Exception type + Twilio error code/status
        # carry the actionable signal without the PII; callers (e.g. the SOS
        # path) log the returned 'error' verbatim and rely on this contract.
        _code = getattr(e, "code", None)
        _status = getattr(e, "status", None)
        _parts = [type(e).__name__]
        if _code is not None:
            _parts.append(f"code={_code}")
        if _status is not None:
            _parts.append(f"status={_status}")
        safe_error = " ".join(_parts)
        logger.error(f"Failed to send SMS to {masked}: {safe_error}")
        return {"success": False, "provider": "twilio", "error": safe_error}


async def send_otp_sms(
    phone: str, otp_code: str, *, twilio_sid: str = "", twilio_token: str = "", twilio_from: str = ""
) -> dict:
    """Send an OTP code via SMS."""
    message = f"Your Spinr verification code is: {otp_code}. It expires in 5 minutes."
    return await send_sms(phone, message, twilio_sid=twilio_sid, twilio_token=twilio_token, twilio_from=twilio_from)
