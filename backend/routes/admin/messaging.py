import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

try:
    from ... import db_supabase
except ImportError:
    import db_supabase

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------- Cloud Messaging ----------


@router.post("/cloud-messaging/send")
async def admin_send_cloud_message(payload: Dict[str, Any]):
    """Send or schedule a cloud message to users/drivers."""
    title = payload.get("title", "")
    description = payload.get("description", "")
    audience = payload.get("audience", "customers")
    msg_type = payload.get("type", "info")
    channels = payload.get("channels")
    if not channels:
        channel = payload.get("channel", "push")
        channels = [channel]
    particular_ids = payload.get("particular_ids") or []
    if not particular_ids:
        pid = payload.get("particular_id")
        if pid:
            particular_ids = [pid]
    scheduled_at = payload.get("scheduled_at")

    if not title or not description:
        raise HTTPException(status_code=400, detail="Title and description are required")

    is_scheduled = bool(scheduled_at)
    status = "scheduled" if is_scheduled else "sent"

    total_recipients = 1
    successful = 0
    failed_count = 0

    if audience in ("particular_customer", "particular_driver"):
        total_recipients = len(particular_ids) if particular_ids else 1
    elif audience == "customers":
        count = await db_supabase.count_documents("users", {"role": "rider"})
        total_recipients = count if count > 0 else 0
    elif audience == "drivers":
        count = await db_supabase.count_documents("users", {"role": "driver"})
        total_recipients = count if count > 0 else 0

    if not is_scheduled:
        try:
            from ...features import send_push_notification
        except ImportError:
            from features import send_push_notification

        target_users: list = []
        if audience in ("particular_customer", "particular_driver"):
            target_users = [{"id": uid} for uid in particular_ids]
        elif audience == "customers":
            target_users = await db.users.find({"role": "rider"}).to_list(10000)
        elif audience == "drivers":
            target_users = await db.users.find({"role": "driver"}).to_list(10000)

        for u in target_users:
            uid = u.get("id") if isinstance(u, dict) else u
            if uid:
                ok = await send_push_notification(uid, title, description)
                if ok:
                    successful += 1
                else:
                    failed_count += 1

        logger.info(f"Cloud message sent to {audience}: {title} (success={successful}, failed={failed_count})")

    doc = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": description,
        "audience": audience,
        "type": msg_type,
        "channel": channels[0],
        "channels": channels,
        "particular_id": particular_ids[0] if particular_ids else None,
        "particular_ids": particular_ids,
        "status": status,
        "scheduled_at": scheduled_at,
        "sent_at": datetime.utcnow().isoformat() if not is_scheduled else None,
        "created_at": datetime.utcnow().isoformat(),
        "total_recipients": total_recipients,
        "successful": successful,
        "failed_count": failed_count,
    }

    try:
        await db_supabase.insert_one("cloud_messages", doc)
    except Exception as e:
        logger.error(f"Failed to insert cloud message: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to save message. The cloud_messages table may not exist yet. Please run migration 06_cloud_messaging.sql.",
        ) from e
    return {"success": True, "message": doc}


@router.get("/cloud-messaging")
async def admin_get_cloud_messages(
    status: Optional[str] = None,
    audience: Optional[str] = None,
    limit: int = Query(100),
    offset: int = Query(0),
):
    """Get cloud messages with optional filters."""
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    if audience:
        filters["audience"] = audience

    try:
        messages = await db_supabase.get_rows(
            "cloud_messages",
            filters,
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception:
        logger.warning("cloud_messages table may not exist yet")
        return []
    return messages


@router.get("/cloud-messaging/stats")
async def admin_get_cloud_message_stats():
    """Get cloud messaging statistics."""
    try:
        all_messages = await db_supabase.get_rows("cloud_messages", {}, limit=10000)
    except Exception:
        logger.warning("cloud_messages table may not exist yet")
        all_messages = []

    total = len(all_messages)
    total_sent = sum(1 for m in all_messages if m.get("status") == "sent")
    total_scheduled = sum(1 for m in all_messages if m.get("status") == "scheduled")
    total_failed = sum(1 for m in all_messages if m.get("status") == "failed")
    total_reached = sum(m.get("successful", 0) for m in all_messages)
    total_recipients = sum(m.get("total_recipients", 0) for m in all_messages)
    success_rate = round((total_reached / total_recipients * 100), 1) if total_recipients > 0 else 0

    return {
        "total_messages": total,
        "total_sent": total_sent,
        "total_scheduled": total_scheduled,
        "total_failed": total_failed,
        "total_recipients_reached": total_reached,
        "success_rate": success_rate,
    }


@router.delete("/cloud-messaging/{message_id}")
async def admin_delete_cloud_message(message_id: str):
    """Cancel/delete a scheduled cloud message."""
    existing = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("cloud_messages", {"id": message_id}, limit=1))
    if not existing:
        raise HTTPException(status_code=404, detail="Message not found")

    if existing.get("status") == "sent":
        raise HTTPException(status_code=400, detail="Cannot delete a sent message")

    await db_supabase.update_one("cloud_messages", {"id": message_id}, {"status": "cancelled"})
    return {"message": "Message cancelled"}
