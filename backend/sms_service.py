"""
SMS Service for Spinr
Supports Twilio for production SMS delivery with console fallback for development.
Credentials are read from DB settings (passed in by caller), not env vars.
"""
import logging

logger = logging.getLogger(__name__)


async def send_sms(to_phone: str, message: str, *,
                   twilio_sid: str = '', twilio_token: str = '', twilio_from: str = '') -> dict:
    """
    Send an SMS message.

    When Twilio credentials are provided: sends real SMS via Twilio.
    Otherwise: logs to console and returns mock result.

    Returns:
        dict with 'success' (bool), 'provider' (str), and optionally 'sid' or 'error'.
    """
    if not all([twilio_sid, twilio_token, twilio_from]):
        # Development fallback — log to console
        logger.info(f'[DEV SMS] To: {to_phone} | Message: {message}')
        return {
            'success': True,
            'provider': 'console',
            'message': 'SMS logged to console (Twilio not configured)'
        }

    try:
        from twilio.rest import Client

        client = Client(twilio_sid, twilio_token)
        sms = client.messages.create(
            body=message,
            from_=twilio_from,
            to=to_phone
        )
        logger.info(f'SMS sent to {to_phone} via Twilio (SID: {sms.sid})')
        return {
            'success': True,
            'provider': 'twilio',
            'sid': sms.sid
        }
    except Exception as e:
        logger.error(f'Failed to send SMS to {to_phone}: {e}')
        return {
            'success': False,
            'provider': 'twilio',
            'error': str(e)
        }


async def send_otp_sms(phone: str, otp_code: str, *,
                       twilio_sid: str = '', twilio_token: str = '', twilio_from: str = '') -> dict:
    """Send an OTP code via SMS."""
    message = f'Your Spinr verification code is: {otp_code}. It expires in 5 minutes.'
    return await send_sms(phone, message,
                          twilio_sid=twilio_sid, twilio_token=twilio_token, twilio_from=twilio_from)
