"""
CASL-compliant marketing email sender.

Wraps utils.email_provider.send_transactional_email with everything a Canadian
commercial electronic message legally needs:

  1. EXPRESS-CONSENT GATE — services.marketing_consent.is_eligible() must pass.
     A non-consenting or suppressed recipient is never contacted; the attempt
     is still logged (status='skipped_no_consent') for the audit trail.
  2. SENDER IDENTIFICATION + PHYSICAL ADDRESS — a footer built from the
     admin-configured company_* settings (name, street, city, province, postal).
  3. ONE-CLICK UNSUBSCRIBE — a signed link in the footer AND a List-Unsubscribe
     / List-Unsubscribe-Post header (RFC 8058) so mail clients show a native
     unsubscribe button.
  4. DEDICATED SENDER — marketing_from_email, separate from the transactional
     receipts sender, so marketing complaints don't harm receipt deliverability.

Every send (and every skip) lands in email_send_log with email_type='marketing'.

PIPEDA: never logs the recipient address — callers pass a PII-safe log_id.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from ..core.config import settings as _cfg
    from ..services import marketing_consent
    from ..utils.email_provider import send_transactional_email
    from ..utils.unsubscribe_token import sign_unsubscribe_token
except ImportError:  # pragma: no cover - direct module imports in tests
    from core.config import settings as _cfg  # type: ignore
    from services import marketing_consent  # type: ignore
    from utils.email_provider import send_transactional_email  # type: ignore
    from utils.unsubscribe_token import sign_unsubscribe_token  # type: ignore


def _coalesce(settings: dict, key: str) -> str:
    """Settings value as a clean string. The settings loader can surface a DB
    NULL as Python None (it overrides the schema default), so a bare
    settings.get() could render 'None' in the footer — coalesce to ''."""
    return (settings.get(key) or "").strip()


def unsubscribe_url(user_id: str) -> str:
    """Signed one-click unsubscribe URL for the email channel."""
    token = sign_unsubscribe_token(user_id=user_id, channel="email")
    base = (_cfg.PUBLIC_API_BASE_URL or "").rstrip("/")
    return f"{base}/api/v1/marketing/unsubscribe?token={token}"


def _postal_address(settings: dict) -> str:
    """CASL-required physical mailing address, assembled from configured parts.
    Falls back gracefully when a part is blank."""
    street = _coalesce(settings, "company_address")
    city = _coalesce(settings, "company_city")
    province = _coalesce(settings, "company_province")
    postal = _coalesce(settings, "company_postal_code")
    locality = " ".join(p for p in (city, province, postal) if p)
    return ", ".join(p for p in (street, locality) if p)


def build_footer_html(settings: dict, unsub_url: str) -> str:
    """The legally-required footer: sender identity, physical address, and a
    visible unsubscribe link."""
    name = _coalesce(settings, "company_name") or "Spinr"
    address = _postal_address(settings)
    addr_line = f"<br/>{address}" if address else ""
    return (
        '<hr style="margin-top:32px;border:none;border-top:1px solid #e2e2e2"/>'
        '<p style="font-size:12px;color:#888;margin-top:12px;line-height:1.5">'
        f"You are receiving this because you opted in to marketing from {name}."
        f"<br/><strong>{name}</strong>{addr_line}"
        f'<br/><a href="{unsub_url}" style="color:#888">Unsubscribe</a> from marketing emails.'
        "</p>"
    )


def build_footer_text(settings: dict, unsub_url: str) -> str:
    name = _coalesce(settings, "company_name") or "Spinr"
    address = _postal_address(settings)
    addr_line = f"\n{address}" if address else ""
    return (
        f"\n\n---\nYou are receiving this because you opted in to marketing from {name}."
        f"\n{name}{addr_line}"
        f"\nUnsubscribe: {unsub_url}"
    )


async def send_marketing_email(
    *,
    to: str,
    user_id: str,
    subject: str,
    html: Optional[str] = None,
    text: Optional[str] = None,
    log_id: str = "-",
    campaign_id: Optional[str] = None,
) -> bool:
    """Send one marketing email iff the recipient has express consent and is not
    suppressed. Appends the CASL footer + List-Unsubscribe header. Returns True
    only when a provider accepted the message.
    """
    # 1. Express-consent + suppression gate. This is the hard legal line.
    if not await marketing_consent.is_eligible("email", user_id=user_id, target=to):
        logger.info("[MARKETING] email skipped (no consent / suppressed) log_id=%s", log_id)
        return False

    try:
        from ..settings_loader import get_app_settings
    except ImportError:  # pragma: no cover
        from settings_loader import get_app_settings  # type: ignore
    settings = await get_app_settings()

    unsub = unsubscribe_url(user_id)

    # 2. Append the CASL footer to whichever bodies the caller supplied.
    html_body = (html + build_footer_html(settings, unsub)) if html else None
    text_body = (text + build_footer_text(settings, unsub)) if text else None

    # 3. One-click unsubscribe headers (RFC 8058). Mailto + HTTPS both supported.
    headers = {
        "List-Unsubscribe": f"<{unsub}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }

    # 4. Dedicated marketing sender (falls back to the SES sender in the provider).
    marketing_from = _coalesce(settings, "marketing_from_email") or None

    return await send_transactional_email(
        to=to,
        subject=subject,
        html=html_body,
        text=text_body,
        log_id=log_id,
        email_type="marketing",
        recipient_user_id=user_id,
        from_email=marketing_from,
        extra_headers=headers,
    )
