"""
Unified transactional-email sender for Spinr.

Provider strategy
-----------------
AWS SES is the PRIMARY provider (materially cheaper at our volume). Resend is
the GUARDRAIL fallback. The decision tree for every send is:

    1. If SES is configured (access key id + secret + from address) → try SES.
       • SES accepted (no exception)         → return True.
       • SES raised                          → log .error and fall through.
    2. If SES is unconfigured OR SES failed → try Resend (if configured).
       • Resend 2xx                          → return True.
       • Resend non-2xx / raised             → log .error and fall through.
    3. Neither provider sent it              → log and return False.

This means an SES outage or mis-configuration never silently drops a receipt:
Resend catches it. When neither is configured (dev/test) we log-only, matching
the previous behaviour of the per-call-site Resend implementations.

PIPEDA: recipient email addresses are never written to logs at error/info
level here — callers pass an already-redacted ``log_id`` (rider_id, "safety",
etc.) for log correlation. Subjects are logged but receipt subjects only
contain a dollar amount, no PII.

boto3 is synchronous, so the SES call runs in a worker thread via
``asyncio.to_thread`` to avoid blocking the event loop.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default SES region — Canada, for PIPEDA data-residency alignment. Overridden
# by the aws_ses_region setting when present.
_DEFAULT_SES_REGION = "ca-central-1"
# Last-resort sender when neither provider has a configured from-address.
_DEFAULT_FROM = "noreply@spinr.ca"


async def _load_settings() -> Dict[str, Any]:
    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    return await get_app_settings()


def _send_ses_sync(
    *,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    source: str,
    to: str,
    subject: str,
    html: Optional[str],
    text: Optional[str],
) -> str:
    """Blocking SES SendRawEmail. Returns the SES MessageId. Raises on failure.

    Uses SendRawEmail (not SendEmail) so the integration works with the
    common ``AmazonSesSendingAccess`` IAM policy, which grants only
    ``ses:SendRawEmail``. We build a MIME message ourselves: a
    multipart/alternative when both text and html are supplied, otherwise a
    single text or html part.

    Runs inside asyncio.to_thread — keep it free of awaitables.
    """
    import boto3

    if not html and not text:
        # SES rejects an empty body; guard so we surface a clear error.
        raise ValueError("send_transactional_email requires html or text")

    message = _build_mime(source=source, to=to, subject=subject, html=html, text=text)

    client = boto3.client(
        "ses",
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )

    resp = client.send_raw_email(
        Source=source,
        Destinations=[to],
        RawMessage={"Data": message.as_string()},
    )
    return resp.get("MessageId", "")


def _build_mime(*, source: str, to: str, subject: str, html: Optional[str], text: Optional[str]):
    """Build the MIME message SES SendRawEmail expects.

    multipart/alternative with both parts when text+html are present (clients
    prefer html, fall back to text); a single MIMEText otherwise.
    """
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if html and text:
        msg = MIMEMultipart("alternative")
        # Plain part first — RFC 2046 says the most-preferred alternative goes last.
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
    elif html:
        msg = MIMEText(html, "html", "utf-8")
    else:
        msg = MIMEText(text or "", "plain", "utf-8")

    msg["Subject"] = subject
    msg["From"] = source
    msg["To"] = to
    return msg


async def _try_ses(
    settings: Dict[str, Any],
    *,
    to: str,
    subject: str,
    html: Optional[str],
    text: Optional[str],
    default_from: str,
    log_id: str,
) -> bool:
    """Attempt delivery via AWS SES. Returns True on success.

    Returns False when SES is unconfigured (so the caller falls through to the
    Resend guardrail) and also on a runtime failure (already logged here).
    """
    access_key_id = (settings.get("aws_ses_access_key_id") or "").strip()
    secret_access_key = (settings.get("aws_ses_secret_access_key") or "").strip()
    if not access_key_id or not secret_access_key:
        return False  # unconfigured — fall through to Resend

    region = (settings.get("aws_ses_region") or "").strip() or _DEFAULT_SES_REGION
    from_email = (settings.get("aws_ses_from_email") or "").strip() or default_from
    source = f"Spinr <{from_email}>"

    try:
        message_id = await asyncio.to_thread(
            _send_ses_sync,
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            source=source,
            to=to,
            subject=subject,
            html=html,
            text=text,
        )
        logger.info(
            "[EMAIL] SES sent log_id=%s subject=%r message_id=%s",
            log_id,
            subject,
            message_id,
        )
        return True
    except Exception:
        # Do not swallow — SES failures must surface so the root cause is
        # fixed. We log .error and let the Resend guardrail take over.
        logger.error(
            "[EMAIL] SES send failed log_id=%s subject=%r — falling back to Resend",
            log_id,
            subject,
            exc_info=True,
        )
        return False


async def _try_resend(
    settings: Dict[str, Any],
    *,
    to: str,
    subject: str,
    html: Optional[str],
    text: Optional[str],
    default_from: str,
    log_id: str,
) -> bool:
    """Attempt delivery via Resend (the guardrail). Returns True on 2xx."""
    resend_key = (settings.get("resend_api_key") or "").strip()
    if not resend_key:
        return False  # unconfigured

    from_email = (settings.get("resend_from_email") or "").strip() or default_from
    payload: Dict[str, Any] = {
        "from": f"Spinr <{from_email}>",
        "to": [to],
        "subject": subject,
    }
    if html:
        payload["html"] = html
    if text:
        payload["text"] = text

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        ok = response.status_code in (200, 201, 202)
        if ok:
            logger.info(
                "[EMAIL] Resend (guardrail) sent log_id=%s subject=%r status=%s",
                log_id,
                subject,
                response.status_code,
            )
        else:
            logger.error(
                "[EMAIL] Resend (guardrail) returned %s log_id=%s subject=%r body=%s",
                response.status_code,
                log_id,
                subject,
                response.text[:200],
            )
        return ok
    except Exception:
        logger.error(
            "[EMAIL] Resend (guardrail) send failed log_id=%s subject=%r",
            log_id,
            subject,
            exc_info=True,
        )
        return False


async def send_transactional_email(
    *,
    to: str,
    subject: str,
    html: Optional[str] = None,
    text: Optional[str] = None,
    default_from: str = _DEFAULT_FROM,
    log_id: str = "-",
) -> bool:
    """Send one transactional email: AWS SES primary, Resend guardrail.

    Args:
        to: Recipient address (single recipient).
        subject: Email subject. For receipts this is only a dollar amount — no PII.
        html: HTML body (optional).
        text: Plain-text body (optional). At least one of html/text is required.
        default_from: Sender used when the chosen provider has no from-address
            configured (each provider prefers its own configured sender).
        log_id: PII-safe identifier (rider_id, "safety", …) for log correlation.
            The recipient address is never logged.

    Returns:
        True if either provider accepted the message, False otherwise.
    """
    if not to:
        logger.warning("[EMAIL] send skipped log_id=%s — no recipient", log_id)
        return False
    if not html and not text:
        logger.error("[EMAIL] send skipped log_id=%s — empty body", log_id)
        return False

    settings = await _load_settings()

    # 1. Primary: AWS SES.
    if await _try_ses(
        settings,
        to=to,
        subject=subject,
        html=html,
        text=text,
        default_from=default_from,
        log_id=log_id,
    ):
        return True

    # 2. Guardrail: Resend (fires when SES unconfigured OR SES failed).
    if await _try_resend(
        settings,
        to=to,
        subject=subject,
        html=html,
        text=text,
        default_from=default_from,
        log_id=log_id,
    ):
        return True

    # 3. Neither provider sent it.
    logger.warning(
        "[EMAIL] no provider configured/succeeded log_id=%s subject=%r — not sent",
        log_id,
        subject,
    )
    return False
