"""Guest customer identity for corporate guest bookings.

A company employee books a ride for a customer by name + phone. The customer
may have no Spinr account — but rides.rider_id is a hard FK to users, and
identity is phone-keyed everywhere (routes/auth.py::verify_otp find-or-creates
by phone). So a guest is simply a users row created here with is_guest=True
and NO session/tokens minted: the moment that phone number logs into the
rider app via OTP, verify_otp matches the same row, flips is_guest off, and
the customer owns their ride history with no claim flow needed.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

try:
    from .. import db_supabase
    from ..utils.pii import redact_phone
    from ..validators import validate_phone
except ImportError:
    import db_supabase  # type: ignore
    from utils.pii import redact_phone  # type: ignore
    from validators import validate_phone  # type: ignore

logger = logging.getLogger(__name__)


async def find_or_create_guest_by_phone(phone: str, first_name: str) -> tuple[dict, bool]:
    """Resolve the customer's users row for a guest booking.

    Returns ``(user, is_existing_app_user)`` — the second element is True when
    the phone belongs to someone who already signed up themselves (they get a
    push + in-app ride instead of the SMS lifecycle).

    DB failures propagate (503 via DatabaseError) — never fall through to
    creating a duplicate account on a failed read (CLAUDE.md, and the exact
    bug class verify_otp guards against).
    """
    _, normalized = validate_phone(phone.strip())  # raises 422-style HTTPException on junk
    phone = normalized or phone.strip()

    existing = await db_supabase.get_user_by_phone(phone)
    if existing:
        # Deactivated identities can't receive rides: pending_deletion is a
        # PIPEDA grace window (the person asked to leave), deleted is purged.
        status_l = str(existing.get("status") or "").lower()
        if status_l in ("pending_deletion", "deleted") or existing.get("deleted_at"):
            raise HTTPException(
                status_code=409,
                detail="This phone number belongs to a deactivated account.",
            )
        return existing, not bool(existing.get("is_guest"))

    new_user = {
        # Field set mirrors verify_otp's new-user insert (routes/auth.py) so a
        # later OTP login treats this row identically — plus is_guest and the
        # customer's first name for driver-facing display.
        "id": str(uuid.uuid4()),
        "phone": phone,
        "first_name": (first_name or "").strip()[:80] or None,
        "role": "rider",
        "is_rider": True,
        "is_driver": False,
        "is_guest": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_complete": False,
        "token_version": 0,
    }
    await db_supabase.create_user(new_user)
    logger.info("guest user created for corporate booking: user_id=%s phone=%s", new_user["id"], redact_phone(phone))
    return new_user, False
