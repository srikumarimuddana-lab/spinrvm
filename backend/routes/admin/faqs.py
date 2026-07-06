import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...utils.rate_limiter import admin_mass_notify_limit
except ImportError:
    import db_supabase
    from utils.rate_limiter import admin_mass_notify_limit

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Pydantic models ----------


class FaqCreateRequest(BaseModel):
    question: str
    answer: str
    category: str = "general"
    audience: str = "both"
    is_active: bool = True


class FaqUpdateRequest(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    category: Optional[str] = None
    audience: Optional[str] = None
    is_active: Optional[bool] = None


class NotificationRequest(BaseModel):
    user_id: Optional[str] = None
    title: str
    body: str
    type: str = "general"
    audience: str = "user"  # user, all, riders, drivers


# ---------- FAQs ----------


@router.get("/faqs")
async def admin_get_faqs():
    """Get all FAQ entries."""
    faqs = await db_supabase.get_rows("faqs", order="created_at", desc=True, limit=500)
    return faqs


@router.post("/faqs")
async def admin_create_faq(faq: FaqCreateRequest):
    """Create a new FAQ entry."""
    doc = {
        "question": faq.question,
        "answer": faq.answer,
        "category": faq.category,
        "audience": faq.audience,
        "is_active": faq.is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row = await db_supabase.insert_one("faqs", doc)
    return {"faq_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@router.put("/faqs/{faq_id}")
async def admin_update_faq(faq_id: str, faq: FaqUpdateRequest):
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

    # Editing question/answer invalidates any stored semantic embedding — clear
    # it so search re-embeds from the new text rather than the old wording.
    if faq.question is not None or faq.answer is not None:
        updates["embedding"] = None
        updates["embedding_model"] = None

    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("faqs", {"id": faq_id}, updates)
    return {"message": "FAQ updated"}


@router.delete("/faqs/{faq_id}")
async def admin_delete_faq(faq_id: str):
    """Delete an FAQ entry."""
    await db_supabase.delete_many("faqs", {"id": faq_id})
    return {"message": "FAQ deleted"}


# ---------- Notifications ----------


@router.post("/notifications/send")
@admin_mass_notify_limit
async def admin_send_notification(request: Request, notification: NotificationRequest):
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
        all_users = await db.get_rows("users", {}, columns="id", limit=10000)
        for u in all_users or []:
            await send_push_notification(u["id"], title, body)
        logger.info(f"Broadcast notification to all users: {title}")
    elif audience == "riders":
        riders = await db.get_rows("users", {"is_rider": True}, columns="id", limit=10000)
        for u in riders or []:
            await send_push_notification(u["id"], title, body)
        logger.info(f"Broadcast notification to all riders: {title}")
    elif audience == "drivers":
        drivers = await db.get_rows("users", {"is_driver": True}, columns="id", limit=10000)
        for u in drivers or []:
            await send_push_notification(u["id"], title, body)
        logger.info(f"Broadcast notification to all drivers: {title}")

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
