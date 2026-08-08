"""Email as a second notification channel for lifecycle events.

Spinr notifies almost entirely by push. That is right for time-critical,
in-ride events (a driver-arrived notice does not belong in an inbox), but it
leaves no durable record for the events people need to be able to go back and
find: an application rejection, a suspension, a document about to expire. Push
is also lossy — an uninstalled app, a revoked token, or a stale FCM
registration means the notice simply never lands, and nothing tells us.

This module is the policy layer that decides which events *also* go to email.
It deliberately mirrors ``utils/driver_status_notifications.py``: message
construction stays with the caller, and there is exactly one side-effecting
function here, so the send contract cannot drift between call sites.

Two classes, and the distinction is legal, not stylistic:

``TRANSACTIONAL``
    Sent regardless of ``notification_preferences.email_enabled``. Under CASL
    these are implied-consent messages the recipient cannot opt out of —
    account status, security, money, tax, and regulatory notices (a driver may
    not opt out of being told their licence expired). This is also the honest
    reading of "the user turned email off": they meant marketing and nudges.

``OPTIONAL``
    Honours ``email_enabled``. Digests, reminders, and nudges.

Before this module ``email_enabled`` was a dead column: persisted, surfaced in
rider-app/app/settings.tsx as an "Email Notifications" toggle, and read by
nothing. A toggle that does nothing is worse than no toggle.

Contract: **best-effort, never fatal.** Every caller has already committed its
state change before reaching here, exactly as with
``notify_driver_status_change``. A send failure logs and returns False; it must
never propagate and undo an admin action or a background-loop claim.

PIPEDA: the recipient's address is never logged. ``email_provider`` takes a
redacted ``log_id`` (the user id) for correlation instead.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from ..utils.email_layout import RenderedEmail
    from ..utils.email_provider import send_transactional_email
except ImportError:  # pragma: no cover - direct module imports in tests
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.email_layout import RenderedEmail  # type: ignore
    from utils.email_provider import send_transactional_email  # type: ignore

logger = logging.getLogger(__name__)


class EmailClass(str, Enum):
    """Whether the recipient's email preference may suppress this message."""

    TRANSACTIONAL = "transactional"
    OPTIONAL = "optional"


# Matches routes/notifications.py's defaults for a user with no preferences
# row — absence of a row means "not yet configured", not "opted out".
_DEFAULT_EMAIL_ENABLED = True


async def _lifecycle_emails_enabled() -> bool:
    """Global kill switch (app_settings.lifecycle_emails_enabled).

    Fails **open**: a settings-load failure must not silently mute account and
    document-expiry notices. The switch exists to turn emails off deliberately,
    not to have them disappear because Supabase hiccuped.
    """
    try:
        settings = await get_app_settings()
    except Exception as exc:
        logger.warning("email policy: settings load failed, defaulting to enabled: %s", exc)
        return True
    return bool(settings.get("lifecycle_emails_enabled", True))


async def _email_opt_in(user_id: str) -> bool:
    """Read notification_preferences.email_enabled for OPTIONAL-class mail.

    Fails **closed**: if we cannot confirm the recipient opted in, we do not
    send. Only optional mail reaches here, so the cost of a false negative is a
    missed nudge, whereas a false positive is mail someone asked not to get.
    """
    try:
        rows = await db_supabase.get_rows("notification_preferences", {"user_id": user_id}, limit=1)
    except Exception as exc:
        logger.warning("email policy: preference load failed for user %s: %s", user_id, exc)
        return False
    if not rows:
        return _DEFAULT_EMAIL_ENABLED
    value = rows[0].get("email_enabled")
    return _DEFAULT_EMAIL_ENABLED if value is None else bool(value)


async def resolve_recipient(user_id: str) -> Optional[dict[str, Any]]:
    """Load the ``users`` row for a recipient, or None if it cannot be read.

    Public because callers that want to personalise copy (a first-name
    greeting) need the row *before* rendering. Pass what you get back as
    ``send_lifecycle_email(user=...)`` so the lookup happens once.
    """
    try:
        return await db_supabase.get_user_by_id(user_id)
    except Exception as exc:
        logger.warning("email policy: user load failed for %s: %s", user_id, exc)
        return None


def can_email(user: dict[str, Any] | None) -> bool:
    """False when there is nobody to email.

    ``users.email`` is nullable and is only captured at profile setup, so any
    account that abandoned onboarding has none — a real population, not a
    theoretical one. A soft-deleted account is tombstoned and must not be
    mailed, matching ``should_notify_driver``'s guard on the push side.
    """
    if not user:
        return False
    if user.get("deleted_at"):
        return False
    return bool((user.get("email") or "").strip())


async def send_lifecycle_email(
    *,
    user_id: str,
    subject: str,
    rendered: RenderedEmail,
    email_type: str,
    email_class: EmailClass = EmailClass.TRANSACTIONAL,
    context: str = "",
    user: dict[str, Any] | None = None,
) -> bool:
    """Send one lifecycle email. Returns True only if a provider accepted it.

    Args:
        user_id: Recipient's ``users.id`` — the delivery key and the PII-safe
            log correlator.
        subject: Subject line. Must not contain PII beyond what the recipient
            already knows about themselves; it is written to logs.
        rendered: Output of ``email_layout.render_email`` — both parts, so the
            provider builds a multipart/alternative.
        email_type: Category recorded in ``email_send_log`` (e.g.
            ``driver_suspended``, ``document_expiry_warning``).
        email_class: See :class:`EmailClass`. Defaults to TRANSACTIONAL, the
            safe default for a lifecycle notice.
        context: Free-text call-site label for log correlation only.
        user: Pre-loaded ``users`` row, when the caller already has one — skips
            a redundant lookup on background-loop paths that iterate drivers.

    Never raises.
    """
    try:
        if not await _lifecycle_emails_enabled():
            logger.info("email policy: suppressed by kill switch (%s) for user %s", context, user_id)
            return False

        recipient = user if user is not None else await resolve_recipient(user_id)
        if not can_email(recipient):
            # Not an error: a driver who never finished profile setup has no
            # address on file. Logged at info so the absence is visible without
            # being alarming, and without the address itself.
            logger.info("email policy: no deliverable address (%s) for user %s", context, user_id)
            return False

        if email_class is EmailClass.OPTIONAL and not await _email_opt_in(user_id):
            logger.info("email policy: opted out (%s) for user %s", context, user_id)
            return False

        return bool(
            await send_transactional_email(
                to=(recipient or {}).get("email", "").strip(),
                subject=subject,
                html=rendered.html,
                text=rendered.text,
                log_id=str(user_id),
                email_type=email_type,
                recipient_user_id=str(user_id),
            )
        )
    except Exception as exc:
        # Best-effort by contract — the caller's state change is already
        # committed. Warning, not error: the push channel and the in-app inbox
        # row still carried the notice.
        logger.warning("lifecycle email failed (%s) for user %s: %s", context, user_id, exc)
        return False
