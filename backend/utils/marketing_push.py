"""
CASL-compliant marketing push sender.

A promotional push (promo codes, feature announcements, re-engagement nudges)
follows the same express-consent model as marketing email/SMS (migration 190:
marketing_preferences.push_opt_in, default false — silence is never consent).
This wraps features.send_push_notification with the one gate that matters:

  EXPRESS-CONSENT GATE — services.marketing_consent.is_eligible("push", …)
  must pass (push_opt_in AND not suppressed) before anything is sent.

Operational/service pushes (ride updates, safety, receipts) do NOT go through
this module — they call features.send_push_notification directly, same as
before. Only broadcasts marked is_marketing=True are gated here.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from ..services import marketing_consent
except ImportError:  # pragma: no cover - direct module imports in tests
    from services import marketing_consent  # type: ignore


async def send_marketing_push(
    *,
    user_id: str,
    title: str,
    body: str,
    data: dict | None = None,
    target_app: str | None = None,
    log_id: str = "-",
) -> bool:
    """Send one marketing push iff the recipient has express push consent.
    Returns True only when the underlying send succeeded."""
    if not await marketing_consent.is_eligible("push", user_id=user_id):
        logger.info("[MARKETING] push skipped (no consent) log_id=%s", log_id)
        return False

    try:
        from .. import features
    except ImportError:  # pragma: no cover
        import features  # type: ignore

    try:
        return bool(await features.send_push_notification(user_id, title, body, data=data, target_app=target_app))
    except Exception:
        logger.error("[MARKETING] push send raised log_id=%s", log_id, exc_info=True)
        return False
