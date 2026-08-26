"""
SOS emergency-contact suppression service (PIA finding R-002).

Spinr sends an unsolicited SOS SMS to a rider's emergency contacts when the
rider triggers an SOS. Those contacts are third parties (not Spinr users) who
never consented and had no opt-out. This module backs a STOP-keyword opt-out
for them, structurally mirroring services/marketing_consent.py's
add_marketing_suppression / is_marketing_suppressed pattern (migration 191)
but NOT reusing that table: marketing_suppressions is keyed to a Spinr
user_id, and an emergency contact has none — it's keyed on phone only
(migration 358, sos_contact_suppressions).

This is NOT a CASL/marketing-consent record. SOS is a safety notification,
not a commercial electronic message, so it isn't gated on express opt-in the
way marketing is. This is a plain do-not-contact suppression list satisfying
the PIA remediation.

SAFETY-CRITICAL: is_suppressed() is fail-open (see its docstring). The SOS
send path must never be silently blocked by a DB hiccup.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from .. import db_supabase
    from ..db_supabase import DuplicateRecordError
    from .marketing_consent import normalize_target
except ImportError:  # pragma: no cover - direct module imports in tests
    import db_supabase  # type: ignore
    from db_supabase import DuplicateRecordError  # type: ignore
    from services.marketing_consent import normalize_target  # type: ignore

_TABLE = "sos_contact_suppressions"


def normalize_phone(phone: str) -> str:
    """Canonical E.164-ish phone for suppression equality.

    Reused from services.marketing_consent.normalize_target("sms", ...) rather
    than duplicated: it's a clean same-package import (marketing_consent has
    no dependency back on this module) and callers on both suppression lists
    must agree on the same NANP normalization (validators.validate_phone) so
    the same STOP reply matches consistently either way.
    """
    return normalize_target("sms", phone)


async def is_suppressed(phone: str) -> bool:
    """True if this phone has opted out of SOS emergency-contact SMS.

    SAFETY-CRITICAL — THIS FUNCTION IS DELIBERATELY FAIL-OPEN.

    On ANY lookup error this returns False (i.e. NOT suppressed → the SOS
    alert still sends). Do NOT change this to fail closed: a bug here that
    fails closed would silently swallow a real emergency alert to a rider's
    emergency contact, which is a far worse outcome than occasionally
    texting someone who opted out. Log loudly instead so the root cause gets
    fixed (per root CLAUDE.md's "do not silently swallow errors" rule) —
    logging loudly and returning False are not in tension here: the log is
    for us, False is what keeps the rider safe.

    The fail-open try/except wraps normalize_phone() too, not just the DB
    lookup — this function's docstring promises "on ANY lookup error", and a
    malformed/unexpected phone value raising inside normalization is still a
    lookup error from the caller's point of view. Today every caller in
    routes/rides/safety.py also wraps its own suppression-check step in a
    fail-open try/except, so this gap was never reachable in practice — but
    this function's own contract should hold standalone, not just because of
    an outer caller-side guard (found in review 2026-08-21, non-blocking at
    the time; closed here).
    """
    try:
        norm = normalize_phone(phone)
        if not norm:
            return False
        row = await db_supabase.find_one(_TABLE, {"phone": norm})
        return row is not None
    except Exception:
        logger.error("[SOS_CONSENT] suppression lookup failed, failing OPEN (SOS will still send)", exc_info=True)
        return False


async def suppress(phone: str, reason: str, source: str) -> None:
    """Idempotently record a STOP opt-out for this phone.

    Mirrors add_marketing_suppression's idempotency: a duplicate STOP (e.g. a
    Twilio webhook redelivery) must not crash on the unique index.
    """
    norm = normalize_phone(phone)
    if not norm:
        return
    try:
        await db_supabase.insert_one(
            _TABLE,
            {"phone": norm, "reason": reason, "source": source},
        )
        logger.info("[SOS_CONSENT] suppressed reason=%s source=%s", reason, source)
    except DuplicateRecordError:
        pass


async def unsuppress(phone: str) -> None:
    """Remove a suppression (re-opt-in via START or equivalent).

    sos_contact_suppressions is a live "currently suppressed?" list, not an
    append-only audit table — this matches marketing_suppressions' actual
    behaviour exactly: admin_delete_marketing_suppression
    (routes/admin/messaging.py) hard-deletes the row rather than soft-marking
    it, and there is no separate append-only event table for suppressions
    (unlike marketing_consent_events, which IS append-only but records
    opt-in/opt-out state, not suppression). A hard DELETE here is therefore
    the correct, precedented choice, not a deviation from convention.
    """
    norm = normalize_phone(phone)
    if not norm:
        return
    await db_supabase.delete_many(_TABLE, {"phone": norm})
    logger.info("[SOS_CONSENT] unsuppressed")
