"""legacy_consent.py — one-time consent-refresh notice for users who never
went through a live Spinr consent flow.

Two populations this closes the gap for, both sharing `users.consent_version
IS NULL` (migration 334):

  1. Legacy-imported riders/drivers (`legacy_import_metadata <> '{}'`) — they
     never saw ANY Spinr consent screen; their account exists purely from the
     old-app CSV/MongoDB import (docs/audit/2026-08-19-full-mongodb-export-
     collection-inventory.md, docs/audit/2026-08-19-legacy-migration-data-
     quality-audit.md's consent-basis finding).
  2. Organic pre-tracking users — signed up before migration 334 existed, so
     `consent_version` is honestly NULL, not fabricated (LGL-3,
     reports/audits/2026-07-22-legal-content-validation-v1.md: "no mechanism
     exists ... to force re-consent" despite CLAUDE.md requiring one).

Deliberately generic, not "legacy-import-only": the same mechanism is meant
to be reused any time `CONSENT_VERSION` (routes/auth.py) bumps for a
material policy change, for any user whose stored version is behind.

Dark-shipped: gated on `app_settings.legacy_consent_notice_enabled`
(default False, per CLAUDE.md's flagged-rollout convention). While off,
GET /consent/status always reports `needs_notice: false` and POST
/consent/accept 404s — no client can be shown or record acceptance of a
notice that isn't live yet.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
    from ..settings_loader import get_app_settings
    from .auth import CONSENT_VERSION
except ImportError:  # pragma: no cover - direct module imports in tests
    import db_supabase  # type: ignore
    from dependencies import get_current_user  # type: ignore
    from routes.auth import CONSENT_VERSION  # type: ignore
    from settings_loader import get_app_settings  # type: ignore

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/consent", tags=["Consent"])


class ConsentStatusResponse(BaseModel):
    needs_notice: bool
    current_version: str


class ConsentAcceptResponse(BaseModel):
    success: bool
    consent_version: str


def _needs_notice(user: dict) -> bool:
    """True when the user's stored consent_version is behind CONSENT_VERSION
    — covers both "never had one" (NULL, imported or pre-tracking organic)
    and a future case where CONSENT_VERSION bumps and an already-notified
    user's stamped version falls behind it again."""
    return (user.get("consent_version") or None) != CONSENT_VERSION


@api_router.get("/status", response_model=ConsentStatusResponse)
async def get_consent_status(current_user: dict = Depends(get_current_user)):
    settings = await get_app_settings()
    if not bool(settings.get("legacy_consent_notice_enabled", False)):
        # Dark: report nothing to show, regardless of the user's actual
        # consent_version state, so a client polling this while the flag is
        # off never renders the notice.
        return ConsentStatusResponse(needs_notice=False, current_version=CONSENT_VERSION)

    user = await db_supabase.get_user_by_id(current_user["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return ConsentStatusResponse(needs_notice=_needs_notice(user), current_version=CONSENT_VERSION)


@api_router.post("/accept", response_model=ConsentAcceptResponse)
async def accept_consent_notice(current_user: dict = Depends(get_current_user)):
    settings = await get_app_settings()
    if not bool(settings.get("legacy_consent_notice_enabled", False)):
        # No live notice to accept — nothing for a client to have shown.
        raise HTTPException(status_code=404, detail="Not found")

    user_id = current_user["id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    # Plain unconditional write, not a guarded upsert: this endpoint only
    # ever moves consent_version FORWARD to the current CONSENT_VERSION, and
    # the caller just affirmatively accepted it — there is no "existing
    # value wins" case to protect here, unlike the SIN/DOB backfill's
    # never-clobber requirement (that guarded against a stale batch
    # overwriting a driver's own later self-entry; this endpoint IS the
    # user's own action, always the freshest signal).
    await db_supabase.update_one(
        "users",
        {"id": user_id},
        {"consent_version": CONSENT_VERSION, "consent_accepted_at": now_iso},
    )
    logger.info("consent notice accepted", extra={"domain": "auth", "surface": "backend"})
    return ConsentAcceptResponse(success=True, consent_version=CONSENT_VERSION)
