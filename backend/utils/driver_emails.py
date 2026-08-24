"""Driver-facing one-time lifecycle email copy and senders.

The driver counterpart to ``utils/rider_emails.py``. Distinct from
``utils/driver_status_notifications.py``, which is status-transition-keyed and
push-first (a driver entering ``active``/``rejected``/``suspended``/``banned``):
this module is for lifecycle emails that are not a status transition at all,
starting with the welcome sent on registration itself, before the driver has
any status a policy map could key on.

Every sender here is best-effort and never raises: registration has already
been committed by the time a sender runs, and an email failure must never
undo it. Every send is TRANSACTIONAL — under CASL an application-progress
notice a driver cannot opt out of, the same reasoning ``rider_emails.py``
applies to the rider welcome.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from ..utils.company_details import load_company_details
    from ..utils.email_layout import render_email
    from ..utils.email_notifications import EmailClass, resolve_recipient, send_lifecycle_email
except ImportError:  # pragma: no cover - direct module imports in tests
    from utils.company_details import load_company_details  # type: ignore
    from utils.email_layout import render_email  # type: ignore
    from utils.email_notifications import (  # type: ignore
        EmailClass,
        resolve_recipient,
        send_lifecycle_email,
    )

logger = logging.getLogger(__name__)


def _greeting(user: dict[str, Any] | None) -> Optional[str]:
    name = ((user or {}).get("first_name") or "").strip()
    return f"Hi {name}," if name else None


async def send_driver_welcome_email(driver: dict[str, Any], user: dict[str, Any] | None = None) -> bool:
    """Sent once, when a driver's application row is first created.

    Registration is the driver counterpart of the rider's first-profile-setup
    welcome: it's the first point the driver has ever been reachable by email
    for this account, and until now nothing told them the application was
    received or what happens next (docs/notification-channel-coverage.md, D1).

    ``user_id`` lives on the driver row, not the driver row's own ``id`` — the
    email policy layer keys everything on ``users.id``, matching every other
    sender in this module family.
    """
    user_id = driver.get("user_id")
    if not user_id:
        return False
    try:
        recipient = user if user is not None else await resolve_recipient(user_id)
        company = await load_company_details()
        return await send_lifecycle_email(
            user_id=user_id,
            user=recipient,
            subject=f"You're in — let's get your {company.app_name} account approved",
            rendered=await render_email(
                greeting=_greeting(recipient),
                heading=f"Welcome to {company.app_name} driving",
                paragraphs=[
                    f"Thanks for signing up to drive with {company.app_name}. Your account is created — "
                    "here's what happens next.",
                    "Upload your driver's licence, vehicle insurance, vehicle inspection, and background "
                    f"check in the {company.app_name} driver app if you haven't already — we'll notify you "
                    "as soon as they've been reviewed.",
                    f"{company.app_name} takes 0% commission — every dollar of the fare goes to you, the "
                    "driver. No per-trip cut, ever.",
                    "While you wait, finish your vehicle details in the app so there's nothing left to do "
                    "once you're approved.",
                    f"Questions any time: {company.support_email}",
                ],
                footnote=(
                    f"Didn't sign up for a {company.app_name} driver account? Contact "
                    f"{company.support_email} and we'll close it."
                ),
                company=company,
            ),
            email_type="driver_welcome",
            email_class=EmailClass.TRANSACTIONAL,
            context="driver_welcome",
        )
    except Exception as exc:
        logger.warning("driver welcome email failed for driver %s: %s", driver.get("id"), exc)
        return False
