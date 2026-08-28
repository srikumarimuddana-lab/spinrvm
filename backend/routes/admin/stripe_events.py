"""Admin endpoints for Stripe webhook event visibility and stuck-event resolution.

Super-admin-only. Surfaces events stuck at processed_at=NULL so ops can
investigate, replay, or dismiss without tailing production logs.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ...dependencies import get_admin_user, require_super_admin
    from ...utils.audit_logger import log_admin_action
except ImportError:
    from dependencies import require_super_admin
    from utils.audit_logger import log_admin_action

try:
    from ..._base_imports import db_supabase
except ImportError:
    pass

try:
    from ... import db_supabase
except ImportError:
    pass  # type: ignore

try:
    from ...db_supabase import (
        DatabaseError,
        claim_stripe_event,
        mark_stripe_event_processed,
        unclaim_stripe_event,
    )
    from ...settings_loader import get_app_settings
except ImportError:
    from db_supabase import (  # type: ignore
        DatabaseError,
        mark_stripe_event_processed,
        unclaim_stripe_event,
    )

try:
    from ...repositories._base import run_sync, supabase
except ImportError:
    from repositories._base import run_sync, supabase  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stripe-events", tags=["Admin – Stripe Events"])


# ── Request / response models ───────────────────────────────────────


class ReplayBody(BaseModel):
    confirm: str = Field(..., description='Must be exactly "REPLAY"')


class DismissBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500, description="Why this event is being dismissed")


# ── Helpers ──────────────────────────────────────────────────────────


async def _query_stuck_events(
    *, limit: int = 50, offset: int = 0, include_payload: bool = False
) -> list[Dict[str, Any]]:
    """Query stripe_events where processed_at IS NULL, oldest first."""
    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    select_cols = "event_id,event_type,received_at"
    if include_payload:
        select_cols += ",payload"

    def _fn():
        res = (
            supabase.table("stripe_events")
            .select(select_cols)
            .is_("processed_at", "null")
            .order("received_at", desc=False)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return getattr(res, "data", None) or []

    return await run_sync(_fn)


async def _get_stuck_event(event_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single stripe_events row by event_id."""
    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        res = (
            supabase.table("stripe_events")
            .select("event_id,event_type,received_at,processed_at,payload")
            .eq("event_id", event_id)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", None) or []
        return data[0] if data else None

    return await run_sync(_fn)


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/stuck")
async def list_stuck_events(
    limit: int = 50,
    offset: int = 0,
    admin: dict = Depends(require_super_admin),
):
    """List Stripe events stuck at processed_at=NULL (oldest first).

    Returns event metadata without the full payload — use the detail
    endpoint for payload inspection.
    """
    limit = min(limit, 100)
    rows = await _query_stuck_events(limit=limit, offset=offset)

    now = datetime.now(timezone.utc)
    items = []
    for row in rows:
        received = row.get("received_at")
        age_minutes = None
        if received:
            try:
                dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
                age_minutes = int((now - dt).total_seconds() / 60)
            except (ValueError, TypeError):
                pass
        items.append(
            {
                "event_id": row["event_id"],
                "event_type": row.get("event_type"),
                "received_at": received,
                "age_minutes": age_minutes,
            }
        )

    return {"items": items, "count": len(items), "offset": offset, "limit": limit}


@router.get("/stuck/count")
async def stuck_event_count(admin: dict = Depends(require_super_admin)):
    """Lightweight count of stuck events for the dashboard banner."""
    if not supabase:
        raise HTTPException(status_code=503, detail="Database not available")

    def _fn():
        res = supabase.table("stripe_events").select("event_id", count="exact").is_("processed_at", "null").execute()
        return getattr(res, "count", 0) or 0

    count = await run_sync(_fn)
    return {"count": count}


@router.get("/{event_id}")
async def get_event_detail(event_id: str, admin: dict = Depends(require_super_admin)):
    """Full detail of a single Stripe event including payload."""
    row = await _get_stuck_event(event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    now = datetime.now(timezone.utc)
    received = row.get("received_at")
    age_minutes = None
    if received:
        try:
            dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
            age_minutes = int((now - dt).total_seconds() / 60)
        except (ValueError, TypeError):
            pass

    return {
        "event_id": row["event_id"],
        "event_type": row.get("event_type"),
        "received_at": received,
        "processed_at": row.get("processed_at"),
        "age_minutes": age_minutes,
        "payload": row.get("payload"),
        "is_stuck": row.get("processed_at") is None,
    }


@router.post("/{event_id}/replay")
async def replay_event(
    event_id: str,
    body: ReplayBody,
    admin: dict = Depends(require_super_admin),
):
    """Replay a stuck Stripe event through the webhook dispatch pipeline.

    Requires body {"confirm": "REPLAY"} to prevent accidental triggers.
    The event must exist and have processed_at=NULL (stuck).
    """
    if body.confirm != "REPLAY":
        raise HTTPException(status_code=400, detail='Body must contain {"confirm": "REPLAY"}')

    row = await _get_stuck_event(event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    if row.get("processed_at") is not None:
        raise HTTPException(status_code=409, detail="Event already processed — cannot replay")

    payload = row.get("payload") or {}
    event_type = row.get("event_type", "")
    data_object = payload.get("data", {}).get("object", {})

    # Unclaim so we can re-claim and re-process
    unclaimed = await unclaim_stripe_event(event_id)
    if not unclaimed:
        raise HTTPException(
            status_code=500,
            detail="Failed to unclaim event — manual DB intervention required",
        )

    # Re-claim (inserts fresh row that mark_stripe_event_processed will stamp)
    is_new = await claim_stripe_event(event_id, event_type, payload)
    if not is_new:
        raise HTTPException(
            status_code=409,
            detail="Event was re-claimed by another process between unclaim and replay",
        )

    # Fetch stripe_secret for invoice.paid subscription recovery
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    try:
        from ..webhooks import _dispatch_stripe_event
    except ImportError:
        from routes.webhooks import _dispatch_stripe_event  # type: ignore

    try:
        result = await _dispatch_stripe_event(
            event_id=event_id,
            event_type=event_type,
            event_payload=payload,
            data_object=data_object,
            stripe_secret=stripe_secret,
        )
    except Exception as exc:
        logger.error(
            "Admin replay of stripe event %s failed: %r",
            event_id,
            exc,
            extra={"domain": "payments", "event_id": event_id},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Replay failed: {exc!s}",
        ) from exc

    await log_admin_action(
        admin,
        "stripe_event_replay",
        "stripe_events",
        event_id,
        {"event_type": event_type, "result": str(result)[:500]},
    )

    return {"replayed": True, "event_id": event_id, "event_type": event_type}


@router.post("/{event_id}/dismiss")
async def dismiss_event(
    event_id: str,
    body: DismissBody,
    admin: dict = Depends(require_super_admin),
):
    """Dismiss a stuck event by stamping processed_at=now().

    Use when the event was manually resolved or is no longer relevant.
    Requires a reason for the audit trail.
    """
    row = await _get_stuck_event(event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    if row.get("processed_at") is not None:
        raise HTTPException(status_code=409, detail="Event already processed")

    await mark_stripe_event_processed(event_id)

    await log_admin_action(
        admin,
        "stripe_event_dismiss",
        "stripe_events",
        event_id,
        {
            "event_type": row.get("event_type"),
            "reason": body.reason,
            "age_minutes": None,
        },
    )

    return {"dismissed": True, "event_id": event_id, "reason": body.reason}
