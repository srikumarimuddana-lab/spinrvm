import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Query

try:
    from ... import db_supabase
    from ...features import send_push_notification
except ImportError:
    import db_supabase
    from features import send_push_notification

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
        "audience": faq.get("audience", "both"),
        "is_active": faq.get("is_active", True),
        "created_at": datetime.now(timezone.utc).isoformat(),
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
    if faq.get("audience") is not None:
        updates["audience"] = faq.get("audience")
    if faq.get("is_active") is not None:
        updates["is_active"] = faq.get("is_active")

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
async def admin_send_notification(notification: Dict[str, Any], background_tasks: BackgroundTasks):
    """Send a notification to a specific user or audience."""
    user_id = notification.get("user_id")
    title = notification.get("title", "")
    body = notification.get("body", "")
    notification_type = notification.get("type", "general")
    audience = notification.get("audience", "user")  # user, all, riders, drivers

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

    async def _broadcast(role_filter: Optional[Dict] = None) -> None:
        users = await db.get_rows("users", role_filter or {}, limit=10000)
        results = await asyncio.gather(
            *[send_push_notification(u["id"], title, body) for u in (users or [])],
            return_exceptions=True,
        )
        failures = sum(1 for r in results if isinstance(r, Exception))
        if failures:
            logger.error(f"Broadcast ({audience!r}): {failures}/{len(results)} FCM sends failed")

    if user_id:
        await db_supabase.insert_one("notifications", notification_doc)
        await send_push_notification(user_id, title, body)
        logger.info(f"Notification dispatched to user {user_id}: {title}")
    elif audience == "all":
        background_tasks.add_task(_broadcast)
        logger.info(f"Broadcast queued for all users: {title}")
    elif audience == "riders":
        background_tasks.add_task(_broadcast, {"role": "rider"})
        logger.info(f"Broadcast queued for riders: {title}")
    elif audience == "drivers":
        background_tasks.add_task(_broadcast, {"role": "driver"})
        logger.info(f"Broadcast queued for drivers: {title}")

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
