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

# Twilio's default http client has no timeout, so a slow/unresponsive Twilio
# can hang the OTP/login path (and SOS notifications) indefinitely, starving
# the asyncio.to_thread threadpool. Bound the HTTP round-trip explicitly.
_TWILIO_HTTP_TIMEOUT_S = 10.0
# Backstop on the thread-pool wait itself, in case the HTTP-level timeout
# doesn't fire (e.g. hang before the socket timeout applies). Kept above the
# HTTP timeout so the more specific Twilio/connection error wins in the
# common case.
_TWILIO_THREAD_TIMEOUT_S = _TWILIO_HTTP_TIMEOUT_S + 5.0


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
        from twilio.http.http_client import TwilioHttpClient
        from twilio.rest import Client

        def _send() -> str:
            # Twilio's REST client is synchronous; run it in the default
            # threadpool so the HTTP round-trip doesn't block the event loop
            # (SOS fires several of these at once). Twilio's default
            # http_client has no timeout, so pass one explicitly — otherwise
            # a slow/unresponsive Twilio can hang this thread indefinitely.
            http_client = TwilioHttpClient(timeout=_TWILIO_HTTP_TIMEOUT_S)
            client = Client(twilio_sid, twilio_token, http_client=http_client)
            return client.messages.create(body=message, from_=twilio_from, to=to_phone).sid

        # Backstop the thread-pool wait too, so a hang the HTTP timeout
        # doesn't catch still can't block the caller (or starve the
        # threadpool) forever.
        sid = await asyncio.wait_for(asyncio.to_thread(_send), timeout=_TWILIO_THREAD_TIMEOUT_S)
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
