"""
Marketing consent + suppression service (CASL).

Single source of truth for "may we send this user MARKETING on this channel?".
Used by the marketing send path, the unsubscribe endpoint, the SES/Twilio
webhooks, and the admin dashboard.

Two records back every decision (migrations 190 + 191):
  • marketing_preferences      — current per-channel express opt-in (default false)
  • marketing_consent_events   — append-only audit of every opt-in / opt-out
  • marketing_suppressions     — per-channel "never market to this target again"

Eligibility = opted in AND not suppressed. For email we ALSO honour the
transactional email_suppressions list (a hard bounce / complaint that blocks
all mail necessarily blocks marketing too).

PIPEDA: this module logs only user_id and channel — never the email/phone.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from .. import db_supabase
    from ..db_supabase import DuplicateRecordError
    from ..utils.email_provider import normalize_email
    from ..validators import validate_phone
except ImportError:  # pragma: no cover - direct module imports in tests
    import db_supabase  # type: ignore
    from db_supabase import DuplicateRecordError  # type: ignore
    from utils.email_provider import normalize_email  # type: ignore
    from validators import validate_phone  # type: ignore

VALID_CHANNELS = ("email", "sms", "push")
_OPT_IN_COL = {"email": "email_opt_in", "sms": "sms_opt_in", "push": "push_opt_in"}
_OPTED_AT_COL = {"email": "email_opted_in_at", "sms": "sms_opted_in_at", "push": "push_opted_in_at"}


def normalize_target(channel: str, value: Optional[str]) -> str:
    """Canonical contact value for suppression equality: E.164 phone or
    lower/trimmed email. Must match what the webhooks store."""
    if not value:
        return ""
    if channel == "sms":
        _, normalized = validate_phone(value, raise_exception=False)
        return (normalized or value).strip()
    return normalize_email(value)


async def get_preferences(user_id: str) -> Dict[str, Any]:
    """Current opt-in state for a user. Missing row → all channels false
    (CASL: no record means no consent)."""
    row = await db_supabase.find_one("marketing_preferences", {"user_id": user_id})
    if not row:
        return {"user_id": user_id, "email_opt_in": False, "sms_opt_in": False, "push_opt_in": False}
    return row


async def set_consent(
    user_id: str,
    channel: str,
    opted_in: bool,
    *,
    source: str,
    consent_version: Optional[str] = None,
) -> None:
    """Set a channel's opt-in state and append a consent-audit event.

    updated_at / opted_in_at are stamped here (the table has no trigger — see
    migration 190). On opt-out we clear opted_in_at so the timestamp always
    reflects an ACTIVE consent.
    """
    if channel not in VALID_CHANNELS:
        raise ValueError(f"invalid channel: {channel}")
    now = datetime.now(timezone.utc)
    update: Dict[str, Any] = {
        "user_id": user_id,
        _OPT_IN_COL[channel]: opted_in,
        _OPTED_AT_COL[channel]: now if opted_in else None,
        "updated_at": now,
    }
    if opted_in and consent_version:
        update["consent_version"] = consent_version
    await db_supabase.update_one("marketing_preferences", {"user_id": user_id}, update, upsert=True)
    await db_supabase.insert_one(
        "marketing_consent_events",
        {
            "user_id": user_id,
            "channel": channel,
            "action": "opt_in" if opted_in else "opt_out",
            "source": source,
            "consent_version": consent_version,
        },
    )
    logger.info(
        "[MARKETING] consent set user_id=%s channel=%s opted_in=%s source=%s",
        user_id,
        channel,
        opted_in,
        source,
    )


async def add_marketing_suppression(
    channel: str,
    target: str,
    *,
    reason: str,
    source: str,
    user_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> bool:
    """Idempotently add a (channel, target) to the marketing suppression list.

    Returns True if a new row was written, False if it was already suppressed
    (the unique index makes this a no-op — webhook redeliveries are safe)."""
    norm = normalize_target(channel, target)
    if not norm:
        return False
    try:
        await db_supabase.insert_one(
            "marketing_suppressions",
            {
                "channel": channel,
                "target": norm,
                "reason": reason,
                "source": source,
                "user_id": user_id,
                "message_id": message_id,
            },
        )
        logger.info(
            "[MARKETING] suppressed channel=%s reason=%s source=%s user_id=%s", channel, reason, source, user_id
        )
        return True
    except DuplicateRecordError:
        return False


async def is_marketing_suppressed(channel: str, target: Optional[str]) -> bool:
    """True if this (channel, target) is on the marketing suppression list, or
    — for email — on the transactional hard-bounce/complaint list. A missing
    target is treated as suppressed: we can't market to a blank address."""
    norm = normalize_target(channel, target)
    if not norm:
        return True
    if await db_supabase.find_one("marketing_suppressions", {"channel": channel, "target": norm}) is not None:
        return True
    if channel == "email":
        # A hard bounce / complaint on the transactional list blocks marketing too.
        if await db_supabase.find_one("email_suppressions", {"email": norm}) is not None:
            return True
    return False


async def is_eligible(channel: str, *, user_id: str, target: Optional[str] = None) -> bool:
    """The one gate the send path calls: opted in AND not suppressed.

    Push has no external suppression list — it's gated purely on push_opt_in.
    Email/SMS require a non-suppressed target.
    """
    if channel not in VALID_CHANNELS:
        return False
    prefs = await get_preferences(user_id)
    if not prefs.get(_OPT_IN_COL[channel]):
        return False
    if channel == "push":
        return True
    if await is_marketing_suppressed(channel, target):
        return False
    return True
