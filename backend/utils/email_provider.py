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
from typing import Any, Dict, Optional, Tuple

try:
    from .pii import redact_email
except ImportError:
    from utils.pii import redact_email  # type: ignore

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


# ── Ops alerting: a deliberately DB-free send path ──────────────────────────
# Provider credentials cached from the last successful settings load, so an
# operational alert can be sent when the database is unavailable. Populated by
# prime_ops_email_settings(); read by send_ops_alert_email().
_ops_settings_cache: Dict[str, Any] = {}


async def prime_ops_email_settings() -> bool:
    """Cache provider credentials while the database is healthy.

    Call this at startup (and opportunistically thereafter). It is the only
    part of the ops-alert path that touches the DB, and it runs at a moment of
    our choosing rather than mid-incident.

    Returns True if credentials are now cached.
    """
    try:
        settings = await _load_settings()
    except Exception as exc:
        logger.warning("[EMAIL] ops settings prime failed: %s", exc)
        return False
    if settings:
        _ops_settings_cache.update(settings)
    return bool(_ops_settings_cache)


async def send_ops_alert_email(
    *,
    to: str,
    subject: str,
    text: str,
    log_id: str = "ops",
) -> bool:
    """Send an operational alert **without touching the database**.

    `send_transactional_email` performs three DB operations per send: it loads
    `app_settings`, queries `email_suppressions`, and INSERTs `email_send_log`.
    That is correct for a receipt and wrong for an alert whose entire purpose
    may be to report that the database is saturated or unreachable — the alert
    would queue on the very pool it is complaining about, and fail exactly when
    it is most needed.

    So this path deliberately drops all three:

    * **Settings** come from the cache primed by `prime_ops_email_settings()`.
      A cold cache falls back to one DB read, which is the best available
      option on a first-ever alert and still better than three.
    * **Suppression check skipped.** Recipients are internal ops addresses, not
      users. Silently suppressing an infrastructure alert because the inbox
      once bounced is a worse failure than sending to a dead address.
    * **`email_send_log` insert skipped.** No audit row is written for ops
      alerts. This is an accepted trade: the delivery outcome is logged to
      stdout instead, and the log table exists for user-facing mail (PIPEDA
      auditability), which this is not.

    Returns True if either provider accepted the message.
    """
    if not to or not text:
        logger.error("[EMAIL] ops alert skipped log_id=%s — missing recipient or body", log_id)
        return False

    settings = dict(_ops_settings_cache)
    if not settings:
        # Cold cache (e.g. first alert after a restart). One DB read is still
        # far better than three, and if the DB is down this simply fails and we
        # fall through to logging — the webhook channel is unaffected.
        try:
            settings = await _load_settings() or {}
            if settings:
                _ops_settings_cache.update(settings)
        except Exception as exc:
            logger.error(
                "[EMAIL] ops alert log_id=%s — no cached credentials and settings "
                "load failed (DB likely unavailable): %s",
                log_id,
                exc,
            )
            return False

    message_id, _ = await _try_ses(
        settings,
        to=to,
        subject=subject,
        html=None,
        text=text,
        default_from=_DEFAULT_FROM,
        log_id=log_id,
    )
    provider = "ses"
    if message_id is None:
        message_id, _ = await _try_resend(
            settings,
            to=to,
            subject=subject,
            html=None,
            text=text,
            default_from=_DEFAULT_FROM,
            log_id=log_id,
        )
        provider = "resend"

    if message_id is None:
        logger.error("[EMAIL] ops alert log_id=%s — no provider accepted the message", log_id)
        return False

    logger.info("[EMAIL] ops alert sent log_id=%s provider=%s to=%s", log_id, provider, redact_email(to))
    return True


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
    attachments: Optional[list] = None,
    extra_headers: Optional[Dict[str, str]] = None,
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

    message = _build_mime(
        source=source,
        to=to,
        subject=subject,
        html=html,
        text=text,
        attachments=attachments,
        extra_headers=extra_headers,
    )

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


def _build_mime(
    *,
    source: str,
    to: str,
    subject: str,
    html: Optional[str],
    text: Optional[str],
    attachments: Optional[list] = None,
    extra_headers: Optional[Dict[str, str]] = None,
):
    """Build the MIME message SES SendRawEmail expects.

    The body is multipart/alternative when both text+html are present (clients
    prefer html, fall back to text), or a single MIMEText. When attachments are
    supplied the whole thing is wrapped in multipart/mixed.

    ``extra_headers`` adds top-level headers (e.g. ``List-Unsubscribe`` for
    marketing mail). Subject/From/To are set here and take precedence.
    """
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if html and text:
        body = MIMEMultipart("alternative")
        # Plain part first — RFC 2046 says the most-preferred alternative goes last.
        body.attach(MIMEText(text, "plain", "utf-8"))
        body.attach(MIMEText(html, "html", "utf-8"))
    elif html:
        body = MIMEText(html, "html", "utf-8")
    else:
        body = MIMEText(text or "", "plain", "utf-8")

    if attachments:
        msg: Any = MIMEMultipart("mixed")
        msg.attach(body)
        for att in attachments:
            content = att.get("content")
            if not content:
                continue
            subtype = (att.get("mime") or "application/octet-stream").partition("/")[2] or "octet-stream"
            part = MIMEApplication(content, _subtype=subtype)
            part.add_header("Content-Disposition", "attachment", filename=att.get("filename", "attachment"))
            msg.attach(part)
    else:
        msg = body

    if extra_headers:
        for hk, hv in extra_headers.items():
            # Skip the headers we set authoritatively below; never let a caller
            # spoof From/To/Subject via extra_headers.
            if hk.lower() in ("subject", "from", "to"):
                continue
            msg[hk] = hv

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
    attachments: Optional[list] = None,
    from_email: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Attempt delivery via AWS SES.

    Returns ``(message_id, error_detail)``:
    - Success: ``(message_id, None)`` — ``message_id`` may be ``""``.
    - Unconfigured (so the caller falls through to the Resend guardrail):
      ``(None, None)``.
    - Runtime failure (already logged here): ``(None, error_detail)`` — a
      PIPEDA-redacted string suitable for persisting to ``email_send_log``.

    ``from_email`` overrides the configured sender (e.g. the marketing sender);
    ``extra_headers`` adds headers such as ``List-Unsubscribe``.
    """
    access_key_id = (settings.get("aws_ses_access_key_id") or "").strip()
    secret_access_key = (settings.get("aws_ses_secret_access_key") or "").strip()
    if not access_key_id or not secret_access_key:
        return None, None  # unconfigured — fall through to Resend

    region = (settings.get("aws_ses_region") or "").strip() or _DEFAULT_SES_REGION
    sender = (from_email or "").strip() or (settings.get("aws_ses_from_email") or "").strip() or default_from
    source = f"Spinr <{sender}>"

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
            attachments=attachments,
            extra_headers=extra_headers,
        )
        logger.info(
            "[EMAIL] SES sent log_id=%s subject=%r message_id=%s",
            log_id,
            subject,
            message_id,
        )
        return message_id or "", None
    except Exception as e:
        # Do not swallow — SES failures must surface so the root cause is fixed.
        # PIPEDA: botocore's MessageRejected echoes the recipient address in its
        # message (e.g. "identities failed the check ... rider@x.com"). Scrub the
        # address out and DON'T emit exc_info (the traceback re-includes it).
        safe = str(e)
        if to:
            safe = safe.replace(to, redact_email(to)).replace(to.lower(), redact_email(to))
        logger.error(
            "[EMAIL] SES send failed log_id=%s subject=%r err=%s — falling back to Resend",
            log_id,
            subject,
            safe,
        )
        return None, safe


async def _try_resend(
    settings: Dict[str, Any],
    *,
    to: str,
    subject: str,
    html: Optional[str],
    text: Optional[str],
    default_from: str,
    log_id: str,
    attachments: Optional[list] = None,
    from_email: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Attempt delivery via Resend (the guardrail).

    Returns ``(message_id, error_detail)``:
    - Success (2xx): ``(message_id, None)`` — ``message_id`` may be ``""``.
    - Unconfigured: ``(None, None)``.
    - Non-2xx / runtime failure (already logged here): ``(None,
      error_detail)`` — a short, PIPEDA-safe string suitable for persisting
      to ``email_send_log`` (never the response body — see the PIPEDA note
      below on why the body itself is never logged or returned).

    ``from_email`` overrides the configured sender; ``extra_headers`` adds
    headers such as ``List-Unsubscribe``.
    """
    import base64

    resend_key = (settings.get("resend_api_key") or "").strip()
    if not resend_key:
        return None, None  # unconfigured

    sender = (from_email or "").strip() or (settings.get("resend_from_email") or "").strip() or default_from
    payload: Dict[str, Any] = {
        "from": f"Spinr <{sender}>",
        "to": [to],
        "subject": subject,
    }
    if extra_headers:
        payload["headers"] = {k: v for k, v in extra_headers.items() if k.lower() not in ("subject", "from", "to")}
    if html:
        payload["html"] = html
    if text:
        payload["text"] = text
    if attachments:
        # Resend wants base64-encoded content per attachment.
        payload["attachments"] = [
            {"filename": a.get("filename", "attachment"), "content": base64.b64encode(a["content"]).decode()}
            for a in attachments
            if a.get("content")
        ]

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
            try:
                return (response.json() or {}).get("id", "") or "", None
            except Exception:
                return "", None
        # PIPEDA: never log response.text — Resend's 4xx validation errors echo
        # the recipient address back in the body. Status code is enough to act.
        logger.error(
            "[EMAIL] Resend (guardrail) returned %s log_id=%s subject=%r",
            response.status_code,
            log_id,
            subject,
        )
        return None, f"Resend HTTP {response.status_code}"
    except Exception as e:
        logger.error(
            "[EMAIL] Resend (guardrail) send failed log_id=%s subject=%r",
            log_id,
            subject,
            exc_info=True,
        )
        return None, str(e)


def normalize_email(email: str) -> str:
    """Canonical form for suppression-list comparison: trimmed + lowercased.

    Used by both the sender (before a suppression check) and the SES webhook
    (before writing a suppression) so the two always agree.
    """
    return (email or "").strip().lower()


async def _is_suppressed(email: str) -> bool:
    """True if the address is on the suppression list (hard bounce/complaint).

    Fail-open: if the suppression lookup itself errors we log .error and return
    False (send anyway). Blocking a receipt on a transient DB hiccup is worse
    than the rare extra send to a freshly-suppressed address — the SES
    account-level suppression list is the backstop for that.
    """
    try:
        try:
            from .. import db_supabase
        except ImportError:
            import db_supabase  # type: ignore
        row = await db_supabase.find_one("email_suppressions", {"email": normalize_email(email)})
        return row is not None
    except Exception:
        logger.error("[EMAIL] suppression lookup failed — sending anyway", exc_info=True)
        return False


async def _log_send(
    *,
    provider: str,
    message_id: Optional[str],
    status: str,
    email_type: Optional[str],
    recipient_user_id: Optional[str],
    error_detail: Optional[str] = None,
) -> None:
    """Best-effort append to email_send_log. Never raises (observability only).

    PIPEDA: stores recipient_user_id, never the email address. ``error_detail``
    is the already-PIPEDA-redacted string _try_ses/_try_resend return on
    failure — never the raw provider response body.
    """
    try:
        try:
            from .. import db_supabase
        except ImportError:
            import db_supabase  # type: ignore
        await db_supabase.insert_one(
            "email_send_log",
            {
                "provider": provider,
                "message_id": message_id or None,
                "status": status,
                "email_type": email_type,
                "recipient_user_id": recipient_user_id,
                "error_detail": error_detail,
            },
        )
    except Exception:
        # A logging failure must never break email delivery.
        logger.error("[EMAIL] email_send_log write failed status=%s provider=%s", status, provider, exc_info=True)


async def send_transactional_email(
    *,
    to: str,
    subject: str,
    html: Optional[str] = None,
    text: Optional[str] = None,
    default_from: str = _DEFAULT_FROM,
    log_id: str = "-",
    email_type: Optional[str] = "transactional",
    recipient_user_id: Optional[str] = None,
    attachments: Optional[list] = None,
    from_email: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> bool:
    """Send one transactional email: AWS SES primary, Resend guardrail.

    Skips addresses on the suppression list (hard bounce/complaint) and records
    every attempt in email_send_log.

    Args:
        to: Recipient address (single recipient).
        subject: Email subject. For receipts this is only a dollar amount — no PII.
        html: HTML body (optional).
        text: Plain-text body (optional). At least one of html/text is required.
        default_from: Sender used when the chosen provider has no from-address
            configured (each provider prefers its own configured sender).
        log_id: PII-safe identifier (rider_id, "safety", …) for log correlation.
            The recipient address is never logged.
        email_type: Category recorded in email_send_log (receipt, dsar, …).
        recipient_user_id: PIPEDA-safe user id recorded in email_send_log.
        from_email: Optional sender override (e.g. the marketing sender).
        extra_headers: Optional top-level headers (e.g. List-Unsubscribe).

    Returns:
        True if either provider accepted the message, False otherwise.
    """
    if not to:
        logger.warning("[EMAIL] send skipped log_id=%s — no recipient", log_id)
        return False
    if not html and not text:
        logger.error("[EMAIL] send skipped log_id=%s — empty body", log_id)
        return False

    # Suppression gate — never send to a hard-bounced / complained address.
    if await _is_suppressed(to):
        logger.warning("[EMAIL] suppressed recipient log_id=%s subject=%r — not sent", log_id, subject)
        await _log_send(
            provider="none",
            message_id=None,
            status="suppressed",
            email_type=email_type,
            recipient_user_id=recipient_user_id,
        )
        return False

    # Fail-open, matching _is_suppressed just above: a transient app_settings
    # read failure (DB hiccup) must degrade to "no provider configured" (the
    # existing, already-handled "neither provider sent it" branch below), not
    # propagate. Before this guard, send_transactional_email violated its own
    # documented "returns bool, never raises" contract — every one of its 12
    # call sites (including the corporate-portal email-OTP send) assumes that
    # contract and calls it unwrapped, so a bare exception here surfaced as a
    # raw 500 instead of a clean "could not send" response. See CLAUDE.md:
    # "don't silently swallow errors" — this still logs loudly, it just
    # doesn't crash the caller for a condition every other branch here
    # already handles gracefully.
    try:
        settings = await _load_settings()
    except Exception:
        logger.error("[EMAIL] app_settings load failed log_id=%s — treating as unconfigured", log_id, exc_info=True)
        settings = {}

    # 1. Primary: AWS SES.
    ses_id, ses_err = await _try_ses(
        settings,
        to=to,
        subject=subject,
        html=html,
        text=text,
        default_from=default_from,
        log_id=log_id,
        attachments=attachments,
        from_email=from_email,
        extra_headers=extra_headers,
    )
    if ses_id is not None:
        await _log_send(
            provider="ses",
            message_id=ses_id,
            status="sent",
            email_type=email_type,
            recipient_user_id=recipient_user_id,
        )
        return True

    # 2. Guardrail: Resend (fires when SES unconfigured OR SES failed).
    resend_id, resend_err = await _try_resend(
        settings,
        to=to,
        subject=subject,
        html=html,
        text=text,
        default_from=default_from,
        log_id=log_id,
        attachments=attachments,
        from_email=from_email,
        extra_headers=extra_headers,
    )
    if resend_id is not None:
        await _log_send(
            provider="resend",
            message_id=resend_id,
            status="sent",
            email_type=email_type,
            recipient_user_id=recipient_user_id,
        )
        return True

    # 3. Neither provider sent it. Prefer the SES error (the primary) when
    # both attempted and failed; a None here just means that provider was
    # never configured/attempted, not that it succeeded.
    error_detail = ses_err or resend_err
    logger.warning(
        "[EMAIL] no provider configured/succeeded log_id=%s subject=%r err=%s — not sent",
        log_id,
        subject,
        error_detail,
    )
    await _log_send(
        provider="none",
        message_id=None,
        status="failed",
        email_type=email_type,
        recipient_user_id=recipient_user_id,
        error_detail=error_detail,
    )
    return False
