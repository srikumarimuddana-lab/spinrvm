import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # type: ignore[assignment]
    from utils.audit_logger import log_admin_action  # type: ignore[assignment]

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()


class CloudMessageRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=500)
    audience: Literal["customers", "drivers", "particular_customer", "particular_driver", "all"] = "customers"
    type: str = "info"
    channels: List[Literal["push", "sms", "email"]] = ["push"]
    particular_ids: Optional[List[str]] = None
    scheduled_at: Optional[str] = None


async def _fan_out_push(message_id: str, target_users: list, title: str, description: str) -> None:
    """Fan-out push notifications concurrently and persist final stats to the cloud_messages record."""
    try:
        from ...features import send_push_notification
    except ImportError:
        from features import send_push_notification

    sem = asyncio.Semaphore(50)

    async def _send_one(uid: str) -> bool:
        async with sem:
            try:
                return bool(await send_push_notification(uid, title, description))
            except Exception:
                return False

    uids = [u.get("id") if isinstance(u, dict) else u for u in target_users]
    uids = [uid for uid in uids if uid]

    results = await asyncio.gather(*[_send_one(uid) for uid in uids], return_exceptions=True)
    successful = sum(1 for r in results if r is True)
    failed_count = len(results) - successful

    logger.info(f"Cloud message {message_id} fan-out complete: success={successful} failed={failed_count}")

    try:
        await db_supabase.update_one(
            "cloud_messages",
            {"id": message_id},
            {"successful": successful, "failed_count": failed_count},
        )
    except Exception:
        logger.error(f"Failed to update cloud_message stats for {message_id}", exc_info=True)


# ---------- Cloud Messaging ----------


@router.post("/cloud-messaging/send")
async def admin_send_cloud_message(
    payload: CloudMessageRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    admin: dict = Depends(get_admin_user),
):
    """Send or schedule a cloud message to users/drivers.

    Immediate sends return 202 Accepted — notifications are fanned out in a
    background task via asyncio.gather + Semaphore(50) so the event loop is
    never blocked by thousands of sequential push-notification awaits.
    Final successful/failed_count figures are written back to the DB record
    once the background task completes.
    """
    title = payload.title
    description = payload.description
    audience = payload.audience
    msg_type = payload.type
    channels = payload.channels
    particular_ids = payload.particular_ids or []
    scheduled_at = payload.scheduled_at

    is_scheduled = bool(scheduled_at)
    status = "scheduled" if is_scheduled else "sent"

    total_recipients = 1

    if audience in ("particular_customer", "particular_driver"):
        total_recipients = len(particular_ids) if particular_ids else 1
    elif audience == "customers":
        count = await db_supabase.count_documents("users", {"role": "rider"})
        total_recipients = count if count > 0 else 0
    elif audience == "drivers":
        count = await db_supabase.count_documents("users", {"role": "driver"})
        total_recipients = count if count > 0 else 0

    target_users: list = []
    if not is_scheduled:
        if audience in ("particular_customer", "particular_driver"):
            target_users = [{"id": uid} for uid in particular_ids]
        elif audience == "customers":
            target_users = await db.get_rows("users", {"role": "rider"}, limit=10000)
        elif audience == "drivers":
            target_users = await db.get_rows("users", {"role": "driver"}, limit=10000)

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
        "sent_at": datetime.now(timezone.utc).isoformat() if not is_scheduled else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_recipients": total_recipients,
        "successful": 0,
        "failed_count": 0,
    }

    try:
        await db_supabase.insert_one("cloud_messages", doc)
    except Exception as e:
        logger.error(f"Failed to insert cloud message: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to save message. The cloud_messages table may not exist yet. Please run migration 06_cloud_messaging.sql.",
        ) from e

    if not is_scheduled:
        background_tasks.add_task(_fan_out_push, doc["id"], target_users, title, description)
        response.status_code = 202

    await log_admin_action(
        admin,
        "cloud_message_sent",
        "cloud_message",
        doc["id"],
        {"audience": audience, "title": title, "status": status, "total_recipients": total_recipients},
    )
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
async def admin_delete_cloud_message(message_id: str, admin: dict = Depends(get_admin_user)):
    """Cancel/delete a scheduled cloud message."""
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("cloud_messages", {"id": message_id}, limit=1)
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Message not found")

    if existing.get("status") == "sent":
        raise HTTPException(status_code=400, detail="Cannot delete a sent message")

    await db_supabase.update_one("cloud_messages", {"id": message_id}, {"status": "cancelled"})
    await log_admin_action(admin, "cloud_message_cancelled", "cloud_message", message_id, {})
    return {"message": "Message cancelled"}
