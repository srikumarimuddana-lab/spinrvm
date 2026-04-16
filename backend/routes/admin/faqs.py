import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

try:
    from ... import db_supabase
except ImportError:
    import db_supabase

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------- FAQs ----------


@router.get("/faqs")
async def admin_get_faqs():
    """Get all FAQ entries."""
    faqs = await db_supabase.get_rows("faqs", order="created_at", desc=True, limit=500)
    return faqs


@router.post("/faqs")
async def admin_create_faq(faq: Dict[str, Any]):
    """Create a new FAQ entry."""
    doc = {
        "question": faq.get("question"),
        "answer": faq.get("answer"),
        "category": faq.get("category", "general"),
        "is_active": faq.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db_supabase.insert_one("faqs", doc)
    return {"faq_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@router.put("/faqs/{faq_id}")
async def admin_update_faq(faq_id: str, faq: Dict[str, Any]):
    """Update an FAQ entry."""
    updates = {}
    if faq.get("question") is not None:
        updates["question"] = faq.get("question")
    if faq.get("answer") is not None:
        updates["answer"] = faq.get("answer")
    if faq.get("category") is not None:
        updates["category"] = faq.get("category")
    if faq.get("is_active") is not None:
        updates["is_active"] = faq.get("is_active")

    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db_supabase.update_one("faqs", {"id": faq_id}, updates)
    return {"message": "FAQ updated"}


@router.delete("/faqs/{faq_id}")
async def admin_delete_faq(faq_id: str):
    """Delete an FAQ entry."""
    await db_supabase.delete_many("faqs", {"id": faq_id})
    return {"message": "FAQ deleted"}


# ---------- Notifications ----------


@router.post("/notifications/send")
async def admin_send_notification(notification: Dict[str, Any]):
    """Send a notification to a specific user or audience."""
    user_id = notification.get("user_id")
    title = notification.get("title", "")
    body = notification.get("body", "")
    notification_type = notification.get("type", "general")
    audience = notification.get("audience", "user")  # user, all, riders, drivers

    # Create notification document
    notification_doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": title,
        "body": body,
        "type": notification_type,
        "audience": audience,
        "sent_at": datetime.utcnow().isoformat(),
        "status": "sent",
        "sent_count": 1 if user_id else 0,
    }

    try:
        from ...features import send_push_notification
    except ImportError:
        from features import send_push_notification

    # If targeting specific user, insert and send
    if user_id:
        await db_supabase.insert_one("notifications", notification_doc)
        # TODO: Integrate with push notification service (FCM)
        logger.info(f"Notification sent to user {user_id}: {title}")
    elif audience == "all":
        all_users = await db.get_rows("users", {}, limit=10000)
        for u in all_users or []:
            await send_push_notification(u["id"], title, body)
        logger.info(f"Broadcast notification to all users: {title}")
    elif audience == "riders":
        riders = await db.get_rows("users", {"role": "rider"}, limit=10000)
        for u in riders or []:
            await send_push_notification(u["id"], title, body)
        logger.info(f"Broadcast notification to all riders: {title}")
    elif audience == "drivers":
        drivers = await db.get_rows("users", {"role": "driver"}, limit=10000)
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
        order="created_at"
        if "created_at"
        in ((lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("notifications", {}, limit=1)) or {})
        else "sent_at",
        desc=True,
        limit=limit,
        offset=offset,
    )
    return notifications
