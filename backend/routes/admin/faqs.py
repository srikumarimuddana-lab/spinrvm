import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import admin_mass_notify_limit
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from utils.audit_logger import log_admin_action
    from utils.rate_limiter import admin_mass_notify_limit

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Pydantic models ----------


class FaqCreateRequest(BaseModel):
    question: str
    answer: str
    category: str = "general"
    # Required, explicit choice — no default. A silent 'both' default is how
    # driver-only FAQs leaked into the rider app; force the author to pick.
    audience: str = Field(..., pattern="^(rider|driver|both)$")
    is_active: bool = True
    # None/[] = global (shown everywhere). One or more service_areas.id values
    # scope the FAQ to users operating in those areas (or their sub-regions,
    # via parent_service_area_id) — e.g. SGI content tagged to SK areas.
    service_area_ids: Optional[List[str]] = None
    # Lower sorts first within a category. Column existed unused for a long
    # time (every read path ordered by created_at instead) — now wired up in
    # both the public /faqs list and this admin list. Default 0 keeps new
    # rows at the top of ties, same as before this field had any effect.
    sort_order: int = 0


class FaqUpdateRequest(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    category: Optional[str] = None
    audience: Optional[str] = Field(default=None, pattern="^(rider|driver|both)$")
    is_active: Optional[bool] = None
    service_area_ids: Optional[List[str]] = None
    sort_order: Optional[int] = None


class NotificationRequest(BaseModel):
    user_id: Optional[str] = None
    title: str
    body: str
    type: str = "general"
    audience: str = "user"  # user, all, riders, drivers


# ---------- FAQs ----------


@router.get("/faqs")
async def admin_get_faqs():
    """Get all FAQ entries, ordered for display: sort_order first (so the
    dashboard table matches what riders/drivers actually see), then
    created_at desc as the tiebreak for rows sharing a sort_order (e.g. the
    default 0 on everything not yet manually reordered)."""
    # Exclude the semantic-search embedding vector — the dashboard never shows
    # it and it would bloat the list to multi-MB once vectors are populated.
    faqs = await db_supabase.get_rows(
        "faqs",
        order="created_at",
        desc=True,
        limit=500,
        columns="id,question,answer,category,audience,service_area_ids,sort_order,is_active,created_at,updated_at",
    )
    # list.sort() is stable, so this layers "sort_order asc" on top of the
    # "created_at desc" order already fetched, without a second DB round trip.
    faqs.sort(key=lambda f: f.get("sort_order") or 0)
    return faqs


@router.post("/faqs")
async def admin_create_faq(faq: FaqCreateRequest, admin: dict = Depends(get_admin_user)):
    """Create a new FAQ entry."""
    # faqs.id is TEXT with no DB default — generate it here.
    doc = {
        "id": str(uuid.uuid4()),
        "question": faq.question,
        "answer": faq.answer,
        "category": faq.category,
        "audience": faq.audience,
        "service_area_ids": faq.service_area_ids,
        "is_active": faq.is_active,
        "sort_order": faq.sort_order,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row = await db_supabase.insert_one("faqs", doc)
    faq_id = str(row.get("id") if row and isinstance(row, dict) else doc["id"])
    await log_admin_action(
        admin,
        "faq_created",
        "faqs",
        faq_id,
        {"question": faq.question, "category": faq.category, "audience": faq.audience},
    )
    return {"faq_id": faq_id}


@router.put("/faqs/{faq_id}")
async def admin_update_faq(faq_id: str, faq: FaqUpdateRequest, admin: dict = Depends(get_admin_user)):
    """Update an FAQ entry."""
    updates: Dict[str, Any] = {}
    if faq.question is not None:
        updates["question"] = faq.question
    if faq.answer is not None:
        updates["answer"] = faq.answer
    if faq.category is not None:
        updates["category"] = faq.category
    if faq.audience is not None:
        updates["audience"] = faq.audience
    if faq.is_active is not None:
        updates["is_active"] = faq.is_active
    if faq.sort_order is not None:
        updates["sort_order"] = faq.sort_order
    # Present in the payload (even as null/[]) → set it; null/[] clears the area
    # scope back to global. Omitted → leave unchanged.
    if "service_area_ids" in faq.model_fields_set:
        updates["service_area_ids"] = faq.service_area_ids

    # Editing question/answer invalidates any stored semantic embedding — clear
    # it so search re-embeds from the new text rather than the old wording.
    if faq.question is not None or faq.answer is not None:
        updates["embedding"] = None
        updates["embedding_model"] = None

    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("faqs", {"id": faq_id}, updates)
        await log_admin_action(admin, "faq_updated", "faqs", faq_id, {"fields": sorted(updates.keys())})
    return {"message": "FAQ updated"}


@router.delete("/faqs/{faq_id}")
async def admin_delete_faq(faq_id: str, admin: dict = Depends(get_admin_user)):
    """Delete an FAQ entry."""
    await db_supabase.delete_many("faqs", {"id": faq_id})
    await log_admin_action(admin, "faq_deleted", "faqs", faq_id, {})
    return {"message": "FAQ deleted"}


# ---------- Notifications ----------


@router.post("/notifications/send")
@admin_mass_notify_limit
async def admin_send_notification(
    request: Request,
    notification: NotificationRequest,
    admin: dict = Depends(get_admin_user),
):
    """Send a notification to a specific user or audience."""
    user_id = notification.user_id
    title = notification.title
    body = notification.body
    notification_type = notification.type
    audience = notification.audience

    notification_doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": title,
        "body": body,
        "type": notification_type,
        "audience": audience,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "status": "sent",
        "sent_count": 1 if user_id else 0,
    }

    try:
        from ...features import send_push_notification
    except ImportError:
        from features import send_push_notification

    if user_id:
        await db_supabase.insert_one("notifications", notification_doc)
        logger.info(f"Notification sent to user {user_id}: {title}")
    # Broadcasts only need the user id for the push fan-out — project it so we
    # don't pull up to 10k base64 profile_image blobs out of the DB.
    elif audience == "all":
        # N10 (ACTION_ITEMS.md): "all" spans both roles with no per-user role
        # lookup here, so it can't map to a single target_app column —
        # target_app stays unset (legacy fcm_token fallback), matching the
        # same accepted precedent routes/admin/messaging.py's
        # _target_app_for_audience already established for its own "all".
        all_users = await db.get_rows("users", {}, columns="id", limit=10000)
        for u in all_users or []:
            await send_push_notification(u["id"], title, body)
        logger.info(f"Broadcast notification to all users: {title}")
    elif audience == "riders":
        riders = await db.get_rows("users", {"is_rider": True}, columns="id", limit=10000)
        for u in riders or []:
            await send_push_notification(u["id"], title, body, target_app="rider")
        logger.info(f"Broadcast notification to all riders: {title}")
    elif audience == "drivers":
        drivers = await db.get_rows("users", {"is_driver": True}, columns="id", limit=10000)
        for u in drivers or []:
            await send_push_notification(u["id"], title, body, target_app="driver")
        logger.info(f"Broadcast notification to all drivers: {title}")

    await log_admin_action(
        admin,
        "notification_sent",
        "notifications",
        notification_doc["id"],
        {"audience": audience, "type": notification_type, "target_user_id": user_id},
    )
    return {"success": True, "notification": notification_doc}


@router.get("/notifications")
async def admin_get_notifications(
    limit: int = Query(50),
    offset: int = Query(0),
    status: Optional[str] = None,
    notification_type: Optional[str] = None,
):
    """Get all sent notifications with optional filters."""
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    if notification_type:
        filters["type"] = notification_type

    notifications = await db_supabase.get_rows(
        "notifications",
        filters,
        order=(
            "created_at"
            if "created_at"
            in ((lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("notifications", {}, limit=1)) or {})
            else "sent_at"
        ),
        desc=True,
        limit=limit,
        offset=offset,
    )
    return notifications
