"""Rider-facing lifecycle email copy and senders.

The rider counterpart to ``utils/driver_status_notifications.py``: all rider
copy lives here, and every send goes through ``email_notifications``' policy
layer, so route files and webhook handlers stay one call long and none of them
re-decides the delivery contract.

Before this module the rider lifecycle emitted **exactly one** transactional
email — the ride receipt. Signup, refunds, wallet top-ups, no-show fees, being
blocked from booking, changing an email address and requesting deletion were
all push-only or silent. Push is transient and lossy; a charge or a security
change needs something the rider can go back and find.

Everything here is TRANSACTIONAL. That is not laziness about the preference
toggle — each of these is either a financial record, a security notice, or a
regulatory confirmation, and under CASL those are implied-consent messages that
a preference must not suppress. The OPTIONAL class exists for digests and
nudges, which this module does not send.

Every sender is best-effort and never raises: callers have already committed
their state change (a Stripe charge, a soft delete, a profile write) and an
email must never undo it.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
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

_TWO_PLACES = Decimal("0.01")


def _money(value: Any) -> str:
    """Format an already-decided amount as ``X.XX``.

    Display only — no arithmetic happens here. The value always arrives already
    computed (a Stripe amount, a persisted fare column, a wallet balance), and
    it goes through ``Decimal(str(...))`` rather than float so a value that
    reached us as a float cannot pick up representation drift on the way to the
    rider's eyes. Per CLAUDE.md, money never touches float in this codebase.
    """
    if value in (None, ""):
        value = 0
    return f"{Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP):.2f}"


def _ride_ref(ride: dict[str, Any] | None) -> str:
    """Rider-visible ride reference, matching the receipt's own choice."""
    if not ride:
        return ""
    return str(ride.get("ride_code") or str(ride.get("id", ""))[:8].upper() or "")


def _greeting(user: dict[str, Any] | None) -> Optional[str]:
    name = ((user or {}).get("first_name") or "").strip()
    return f"Hi {name}," if name else None


async def _send(
    *,
    user_id: str,
    user: dict[str, Any] | None,
    subject: str,
    heading: str,
    paragraphs: list[str],
    email_type: str,
    footnote: Optional[str] = None,
    to_override: Optional[str] = None,
    company: Optional[Any] = None,
) -> bool:
    """Render and hand off. Resolves the user row once if the caller has none.

    ``company`` is passed through so a sender that already loaded the identity
    to interpolate the support address into its copy does not read settings
    twice — and so the footer and that copy cannot disagree about where to
    write for help.
    """
    try:
        recipient = user if user is not None else await resolve_recipient(user_id)
        return await send_lifecycle_email(
            user_id=user_id,
            user=recipient,
            subject=subject,
            rendered=await render_email(
                greeting=_greeting(recipient),
                heading=heading,
                paragraphs=paragraphs,
                footnote=footnote,
                company=company,
            ),
            email_type=email_type,
            email_class=EmailClass.TRANSACTIONAL,
            context=email_type,
            to_override=to_override,
        )
    except Exception as exc:
        logger.warning("rider email failed (%s) for user %s: %s", email_type, user_id, exc)
        return False


# ── Account ─────────────────────────────────────────────────────────────────


async def send_welcome_email(user: dict[str, Any]) -> bool:
    """Sent once, when a rider first completes their profile.

    Profile setup is where the email address is first captured, so this is the
    earliest point we can reach them — and it doubles as a confirmation that
    the address we hold actually works, which nothing else in the rider flow
    ever checks.
    """
    company = await load_company_details()
    return await _send(
        company=company,
        user_id=user["id"],
        user=user,
        subject="Welcome to Spinr",
        heading="Welcome to Spinr",
        paragraphs=[
            "Your account is ready. You can book a ride from the Spinr app whenever you need one.",
            "Spinr is Saskatchewan-built and takes 0% commission — every dollar of the fare "
            "goes to your driver. You'll always see the full price before you confirm a ride, "
            "with GST and PST shown as separate line items on your receipt.",
            f"Questions any time: {company.support_email}",
        ],
        email_type="rider_welcome",
    )


async def send_email_changed_notice(
    user: dict[str, Any],
    old_email: str,
) -> bool:
    """Security notice to the address that was just replaced.

    The point is the **old** address: if someone else changed it, the person
    who owned the account is only reachable there, and a silent change is how
    an account takeover becomes permanent. Sent via ``to_override`` because by
    now the user row already holds the new address.
    """
    old = (old_email or "").strip()
    if not old:
        return False
    company = await load_company_details()
    return await _send(
        company=company,
        user_id=user["id"],
        user=user,
        subject="The email on your Spinr account was changed",
        heading="Your Spinr email address was changed",
        paragraphs=[
            "The email address on your Spinr account was just changed, so this is the last "
            "message this address will receive.",
            f"If you made this change, nothing more is needed. If you did not, contact "
            f"{company.support_email} straight away — your account may have been accessed by someone else.",
        ],
        email_type="rider_email_changed",
        to_override=old,
    )


async def send_account_deletion_notice(user: dict[str, Any], scheduled_at: str) -> bool:
    """Confirms a PIPEDA deletion request and states what is kept, and why."""
    when = (scheduled_at or "")[:10]
    company = await load_company_details()
    return await _send(
        company=company,
        user_id=user["id"],
        user=user,
        subject="Your Spinr account has been deactivated",
        heading="Your account has been deactivated",
        paragraphs=[
            "We've received your deletion request and your Spinr account is now closed. "
            "You can reactivate it any time by signing in with your phone number.",
            "Your ride records are kept, still linked to you, because the Saskatchewan "
            "Transportation Act and Canadian tax rules require us to hold them for seven "
            f"years. After that they are permanently deleted{f' — currently scheduled for {when}' if when else ''}. "
            "Location traces are removed sooner, at three years.",
            f"If you did not request this, contact {company.support_email} immediately.",
        ],
        email_type="rider_account_deletion",
    )


# ── Money ───────────────────────────────────────────────────────────────────


async def send_refund_email(
    user_id: str,
    amount: Any,
    ride: dict[str, Any] | None = None,
    user: dict[str, Any] | None = None,
) -> bool:
    """A refund is a financial record; a push that scrolls away is not one."""
    ref = _ride_ref(ride)
    company = await load_company_details()
    return await _send(
        company=company,
        user_id=user_id,
        user=user,
        subject=f"Your Spinr refund of ${_money(amount)}",
        heading=f"Refund processed — ${_money(amount)} CAD",
        paragraphs=[
            f"We've refunded ${_money(amount)} CAD"
            + (f" for ride {ref}." if ref else ".")
            + " Your bank has processed it from our side.",
            "Depending on your bank, it can take 5–10 business days to appear on your "
            "statement. It will be credited to the original payment method.",
        ],
        email_type="rider_refund",
        footnote=f"Not expecting this refund? Contact {company.support_email}.",
    )


async def send_wallet_topup_email(
    user_id: str,
    amount: Any,
    new_balance: Any = None,
    user: dict[str, Any] | None = None,
) -> bool:
    """Receipt for money added to the Spinr wallet."""
    company = await load_company_details()
    paragraphs = [f"${_money(amount)} CAD has been added to your Spinr wallet."]
    if new_balance not in (None, ""):
        paragraphs.append(f"Your wallet balance is now ${_money(new_balance)} CAD.")
    paragraphs.append("Your wallet is used automatically on your next ride unless you pick another payment method.")
    return await _send(
        company=company,
        user_id=user_id,
        user=user,
        subject=f"Spinr wallet top-up — ${_money(amount)}",
        heading=f"Wallet topped up — ${_money(amount)} CAD",
        paragraphs=paragraphs,
        email_type="rider_wallet_topup",
        footnote=f"Didn't make this top-up? Contact {company.support_email}.",
    )


async def send_no_show_fee_email(
    user_id: str,
    amount: Any,
    ride: dict[str, Any] | None = None,
    user: dict[str, Any] | None = None,
) -> bool:
    """A charge the rider did not choose to make needs a written record."""
    ref = _ride_ref(ride)
    company = await load_company_details()
    return await _send(
        company=company,
        user_id=user_id,
        user=user,
        subject=f"No-show fee charged — ${_money(amount)}",
        heading=f"A ${_money(amount)} no-show fee was charged",
        paragraphs=[
            "Your driver arrived and waited at the pickup point, but the ride didn't start, "
            f"so a no-show fee of ${_money(amount)} CAD was charged" + (f" for ride {ref}." if ref else "."),
            "The fee goes to the driver for the time and distance they spent getting to you.",
        ],
        email_type="rider_no_show_fee",
        footnote=f"Think this is wrong? Contact {company.support_email} and we'll review it.",
    )


async def send_payment_blocked_email(
    user_id: str,
    amount: Any,
    ride: dict[str, Any] | None = None,
    user: dict[str, Any] | None = None,
) -> bool:
    """Sent when retries are exhausted and the rider can no longer book.

    Until now this notified admins only, so the rider met a booking failure
    with no explanation and no way to know what to fix.
    """
    ref = _ride_ref(ride)
    company = await load_company_details()
    return await _send(
        company=company,
        user_id=user_id,
        user=user,
        subject="Action needed: your Spinr payment didn't go through",
        heading="We couldn't complete your payment",
        paragraphs=[
            f"We tried several times to charge ${_money(amount)} CAD"
            + (f" for ride {ref}" if ref else "")
            + ", and your payment method declined each time.",
            "You won't be able to book another ride until this is settled. Open the Spinr app "
            "and add or update a payment method, and we'll retry the outstanding amount.",
            f"If you think your card is fine, contact {company.support_email} and we'll look into it.",
        ],
        email_type="rider_payment_blocked",
    )


async def send_email_verification_code(
    user: dict[str, Any],
    code: str,
    expiry_minutes: int,
) -> bool:
    """OTP for the rider self-serve "verify your email" flow (N14,
    ACTION_ITEMS.md — ``POST /users/verify-email/request``).

    Distinct from `send_welcome_email`: welcome only proves the address
    didn't hard-bounce at send time, this proves the rider can actually read
    what lands in it. Not sent automatically on any lifecycle event — issued
    only when explicitly requested via the verify-email endpoint, so unlike
    every other sender in this module it is never fired from a route the
    rider didn't just call for exactly this purpose.
    """
    company = await load_company_details()
    return await _send(
        company=company,
        user_id=user["id"],
        user=user,
        subject="Your Spinr verification code",
        heading="Verify your email",
        paragraphs=[
            f"Your Spinr verification code is {code}.",
            f"It expires in {expiry_minutes} minutes. If you didn't request it, you can ignore this email.",
        ],
        email_type="rider_email_verification",
    )
