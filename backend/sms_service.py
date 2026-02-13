import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

async def send_otp_sms(
    phone: str,
    otp_code: str,
    twilio_sid: Optional[str] = None,
    twilio_token: Optional[str] = None,
    twilio_from: Optional[str] = None
) -> Dict[str, Any]:
    """
    Sends an OTP via SMS using Twilio if credentials are provided.
    Falls back to logging the OTP if Twilio is not configured.
    """

    # 1. Check if Twilio is fully configured
    if twilio_sid and twilio_token and twilio_from:
        try:
            from twilio.rest import Client
            client = Client(twilio_sid, twilio_token)

            message = client.messages.create(
                body=f"Your Spinr verification code is: {otp_code}",
                from_=twilio_from,
                to=phone
            )

            logger.info(f"SMS sent via Twilio to {phone}: SID {message.sid}")
            return {"success": True, "sid": message.sid}

        except ImportError:
            logger.error("Twilio library not installed. Add 'twilio' to requirements.txt")
            return {"success": False, "error": "Twilio library missing"}
        except Exception as e:
            logger.error(f"Twilio SMS failed: {e}")
            return {"success": False, "error": str(e)}

    # 2. Fallback: Log the OTP (Development Mode)
    else:
        logger.warning(f"Twilio not configured. Mocking SMS to {phone}. OTP: {otp_code}")
        # In a real production app, you might want to return False here if SMS is mandatory.
        # But for this hybrid setup, we allow mocking if credentials aren't set.
        return {"success": True, "message": "Twilio not configured; OTP logged."}
