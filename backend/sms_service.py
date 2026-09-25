"""
SMS Service for Spinr
Supports Twilio for production SMS delivery with console fallback for development.
Credentials are read from DB settings (passed in by caller), not env vars.
"""

import asyncio
import contextvars

from loguru import logger

try:
    from .utils.bounded_executor import BoundedExecutor, ExecutorSaturated
    from .utils.pii import redact_phone
except ImportError:
    from utils.bounded_executor import BoundedExecutor, ExecutorSaturated
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

# Twilio sends run on dedicated, bounded executors, not the event loop's
# shared default pool (asyncio.to_thread). A stalled DNS lookup is not covered
# by the HTTP timeout, and asyncio.wait_for abandons (cannot kill) a hung
# thread -- so on the shared pool a Twilio/DNS outage could pin the threads
# WebSocket auth, Stripe and other to_thread callers need. Here an outage pins
# at most max_workers threads per pool; once workers AND queue are full a new
# send fails fast (ExecutorSaturated -> the usual {"success": False, ...})
# instead of queueing without limit.
#
# Three pools so neither a public OTP flood nor a bulk broadcast can starve
# SOS:
# - SOS (send_sos_sms: routes/rides/safety.py ride + rideless SOS only, <= 3
#   contacts per trigger -- MAX_EMERGENCY_CONTACTS): 4 workers covers one
#   fan-out in a single round trip; the 28-deep queue holds ~10 concurrent
#   SOS triggers, far beyond anything seen, before failing fast.
# - OTP (send_otp_sms: one SMS per /auth/send-otp, rate-limited 6/min per
#   client): 4 workers ~= 4+ sends/s at a sub-second Twilio round trip, far
#   above organic login volume; the small queue absorbs a burst, past that a
#   send fails fast and the rider is told to retry.
# - Everything else via send_sms. The bulk caller is admin cloud messaging
#   (routes/admin/messaging.py _fan_out): asyncio.Semaphore(50) per broadcast,
#   each recipient's SMS awaited in turn, so <= 50 in flight per broadcast;
#   marketing SMS (utils/marketing_sms.py) is per-recipient and only reached
#   through that same semaphore. The rest are one SMS per event (spawned guest
#   ride notices, the SOS contact opt-out notice). 256 admission slots = one
#   broadcast plus ~200 concurrent small sends, or ~5 simultaneous broadcasts,
#   before anything fails fast -- the bound protects the default executor, it
#   is not meant to drop healthy work. 16 workers so the whole 256 drains in
#   ~8 s at a healthy ~0.5 s Twilio round trip, inside the 15 s wait; a deeper
#   queue than the workers can drain would just turn into TimeoutErrors.
_SOS_SMS_EXECUTOR = BoundedExecutor(max_workers=4, queue_size=28, thread_name_prefix="spinr-sms-sos")
_OTP_SMS_EXECUTOR = BoundedExecutor(max_workers=4, queue_size=8, thread_name_prefix="spinr-sms-otp")
_SMS_EXECUTOR = BoundedExecutor(max_workers=16, queue_size=240, thread_name_prefix="spinr-sms")


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
    return await _send_sms_on(
        _SMS_EXECUTOR, to_phone, message, twilio_sid=twilio_sid, twilio_token=twilio_token, twilio_from=twilio_from
    )


async def send_sos_sms(
    to_phone: str, message: str, *, twilio_sid: str = "", twilio_token: str = "", twilio_from: str = ""
) -> dict:
    """send_sms for SOS emergency-contact alerts, on the dedicated SOS pool so
    an OTP flood or a bulk broadcast can never take its capacity. Same
    arguments and return shape as send_sms."""
    return await _send_sms_on(
        _SOS_SMS_EXECUTOR, to_phone, message, twilio_sid=twilio_sid, twilio_token=twilio_token, twilio_from=twilio_from
    )


async def _send_sms_on(
    executor: BoundedExecutor, to_phone: str, message: str, *, twilio_sid: str, twilio_token: str, twilio_from: str
) -> dict:
    masked = redact_phone(to_phone)
    if not all([twilio_sid, twilio_token, twilio_from]):
        # Development fallback — log to console (PII-safe: phone redacted, message dropped)
        logger.info(f"[DEV SMS] To: {masked} (Twilio not configured)")
        return {"success": True, "provider": "console", "message": "SMS logged to console (Twilio not configured)"}

    try:
        from twilio.http.http_client import TwilioHttpClient
        from twilio.rest import Client

        def _send() -> str:
            # Twilio's REST client is synchronous; run it on a dedicated
            # bounded executor so the HTTP round-trip doesn't block the event
            # loop (SOS fires several of these at once). Twilio's default
            # http_client has no timeout, so pass one explicitly — otherwise
            # a slow/unresponsive Twilio can hang this thread indefinitely.
            http_client = TwilioHttpClient(timeout=_TWILIO_HTTP_TIMEOUT_S)
            client = Client(twilio_sid, twilio_token, http_client=http_client)
            return client.messages.create(body=message, from_=twilio_from, to=to_phone).sid

        # Backstop the thread-pool wait too, so a hang the HTTP timeout
        # doesn't catch still can't block the caller forever. run_in_executor
        # raises ExecutorSaturated synchronously when the pool is full; a
        # still-queued send whose wait times out is cancelled and never runs.
        # Run inside a copy of the caller's context, as asyncio.to_thread did:
        # Sentry's default stdlib/threading integrations read the current
        # scope/span from contextvars when instrumenting the Twilio HTTP call,
        # and pool threads would otherwise see whichever request's context
        # was live when the thread was first spawned.
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        sid = await asyncio.wait_for(loop.run_in_executor(executor, ctx.run, _send), timeout=_TWILIO_THREAD_TIMEOUT_S)
        logger.info(f"SMS sent to {masked} via Twilio (SID: {sid})")
        return {"success": True, "provider": "twilio", "sid": sid}
    except ExecutorSaturated:
        pool = {id(_SOS_SMS_EXECUTOR): "sos", id(_OTP_SMS_EXECUTOR): "otp"}.get(id(executor), "general")
        logger.bind(sms_pool=pool).error(f"Failed to send SMS to {masked}: ExecutorSaturated (sms pool={pool} full)")
        return {"success": False, "provider": "twilio", "error": "ExecutorSaturated"}
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
    # OTP pool, not the general one: an OTP flood must not starve SOS sends.
    return await _send_sms_on(
        _OTP_SMS_EXECUTOR, phone, message, twilio_sid=twilio_sid, twilio_token=twilio_token, twilio_from=twilio_from
    )
