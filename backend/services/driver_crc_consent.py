"""
Driver CRC/VSC background-check consent service (PIPEDA).

Single source of truth for "has this driver given explicit, current consent
to the Criminal Record Check / Vulnerable Sector Check?". Separate from the
general Privacy Policy consent — criminal-record information is a sensitive
PIPEDA category and deserves its own purpose-built, versioned consent
record, captured at driver onboarding and re-confirmed whenever the consent
text (legal_documents audience='driver', doc_type='background-check-consent')
changes version, matching how a document's `version` is used elsewhere in
this codebase (see routes/legal_documents.py).

Two records back every decision (migration 319):
  • driver_crc_consents        — current consent state, one row per driver
  • driver_crc_consent_events  — append-only audit of every consent given

PIPEDA: this module logs only driver_id — never a name, license number, or
background-check result. See docs/legal/background-check-consent.md for the
consent language itself.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover - direct module imports in tests
    import db_supabase  # type: ignore

VALID_SOURCES = ("driver_app", "admin")


async def get_consent_status(driver_id: str) -> Dict[str, Any]:
    """Current consent state for a driver. Missing row → not consented
    (PIPEDA: no record means no consent)."""
    row = await db_supabase.find_one("driver_crc_consents", {"driver_id": driver_id})
    if not row:
        return {"driver_id": driver_id, "consented": False, "consent_version": None, "consented_at": None}
    return row


async def is_consent_current(driver_id: str, current_version: int) -> bool:
    """True only if the driver has consented AND their consent_version
    matches the version currently being served — a material change to the
    consent text (a version bump) requires re-consent, not a silent
    carry-forward of an old agreement."""
    status = await get_consent_status(driver_id)
    return bool(status.get("consented")) and status.get("consent_version") == current_version


async def record_consent(
    driver_id: str,
    *,
    consent_version: Optional[int],
    source: str,
) -> None:
    """Record (or renew) a driver's CRC/VSC consent and append a consent
    audit event.

    Called from the CRC consent screen at onboarding (source='driver_app')
    on first consent, and again whenever the served consent_version differs
    from what's on file (a 'renew' event in the audit trail, same row
    updated in driver_crc_consents since only the latest state matters
    there).
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid source: {source}")

    existing = await db_supabase.find_one("driver_crc_consents", {"driver_id": driver_id})
    action = "renew" if existing and existing.get("consented") else "consent"

    now = datetime.now(timezone.utc)
    await db_supabase.update_one(
        "driver_crc_consents",
        {"driver_id": driver_id},
        {
            "driver_id": driver_id,
            "consented": True,
            "consent_version": consent_version,
            "consented_at": now,
            "updated_at": now,
        },
        upsert=True,
    )
    await db_supabase.insert_one(
        "driver_crc_consent_events",
        {
            "driver_id": driver_id,
            "action": action,
            "consent_version": consent_version,
            "source": source,
        },
    )
    logger.info(
        "[DRIVER_CRC_CONSENT] %s driver_id=%s consent_version=%s source=%s",
        action,
        driver_id,
        consent_version,
        source,
    )
