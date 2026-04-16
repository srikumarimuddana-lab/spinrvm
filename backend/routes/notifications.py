"""
notifications.py – In-app notification system for Spinr.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
except ImportError:
    import db_supabase
    from dependencies import get_current_user

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationCreate(BaseModel):
    title: str
    body: str
    type: str = "general"  # ride_update | promotion | safety | general
    data: Optional[Dict[str, Any]] = None


class PreferencesUpdate(BaseModel):
    push_enabled: Optional[bool] = None
    email_enabled: Optional[bool] = None
    sms_enabled: Optional[bool] = None
    ride_updates: Optional[bool] = None
    promotions: Optional[bool] = None
    safety_alerts: Optional[bool] = None


class RegisterTokenRequest(BaseModel):
    token: str
    platform: str = "unknown"


@api_router.post("/register-token")
async def register_push_token(body: RegisterTokenRequest, current_user: dict = Depends(get_current_user)):
    """Save FCM push token for this user/device.

    Writes to two places:

    * `push_tokens` table — the canonical, multi-device store keyed by
      (user_id, platform). Used for per-device bookkeeping and future
      multi-device delivery.
    * `users.fcm_token` column — the "current device" shortcut used by
      `features.send_push_notification`, which looks the token up here
      rather than joining against `push_tokens`. Keeping both in sync
      means a single registration actually makes the notification
      delivery path work.

    When the same user registers a new token (e.g. they reinstalled the
    app), the new token replaces the old one on both rows.
    """
    token = body.token
    platform = body.platform

    # Upsert: one token per user per platform
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "push_tokens",
            {
                "user_id": current_user["id"],
                "platform": platform,
            },
            limit=1,
        )
    )

    if existing:
        await db_supabase.update_one(
            "push_tokens", {"id": existing["id"]}, {"token": token, "updated_at": datetime.utcnow().isoformat()}
        )
    else:
        await db_supabase.insert_one(
            "push_tokens",
            {
                "id": str(uuid.uuid4()),
                "user_id": current_user["id"],
                "token": token,
                "platform": platform,
                "created_at": datetime.utcnow().isoformat(),
            },
        )

    # Mirror to users.fcm_token so features.send_push_notification can find it.
    # This is best-effort: if the column doesn't exist yet (pre sql/03_features),
    # the update is a no-op rather than a hard failure.
    try:
        await db.users.update_one({"id": current_user["id"]}, {"$set": {"fcm_token": token}})
    except Exception as exc:
        logger.warning(f"Failed to mirror FCM token onto users.fcm_token: {exc}")

    logger.info(f"FCM token registered for user {current_user['id']} ({platform})")
    return {"success": True}


@api_router.get("")
async def get_notifications(
    limit: int = Query(30),
    offset: int = Query(0),
    unread_only: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    """Get user's notifications (paginated)."""
    filters: Dict[str, Any] = {"user_id": current_user["id"]}
    if unread_only:
        filters["is_read"] = False

    notifications = await db_supabase.get_rows(
        "notifications",
        filters,
        order="created_at",
        desc=True,
        limit=limit,
        offset=offset,
    )

    # Count unread
    unread_count = 0
    try:
        unread_count = await db_supabase.count_documents(
            "notifications", {"user_id": current_user["id"], "is_read": False}
        )
    except Exception:  # noqa: S110
        pass

    return {"notifications": notifications, "unread_count": unread_count}


@api_router.put("/{notification_id}/read")
async def mark_as_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    """Mark a single notification as read."""
    await db_supabase.update_one(
        "notifications",
        {"id": notification_id, "user_id": current_user["id"]},
        {"is_read": True, "read_at": datetime.utcnow().isoformat()},
    )
    return {"success": True}


@api_router.put("/read-all")
async def mark_all_read(current_user: dict = Depends(get_current_user)):
    """Mark all notifications as read for the current user."""
    await db_supabase.update_one(
        "notifications",
        {"user_id": current_user["id"], "is_read": False},
        {"is_read": True, "read_at": datetime.utcnow().isoformat()},
    )
    return {"success": True}


@api_router.get("/preferences")
async def get_preferences(current_user: dict = Depends(get_current_user)):
    """Get user's notification preferences."""
    prefs = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("notification_preferences", {"user_id": current_user["id"]}, limit=1)
    )
    if not prefs:
        # Return defaults
        return {
            "push_enabled": True,
            "email_enabled": True,
            "sms_enabled": False,
            "ride_updates": True,
            "promotions": True,
            "safety_alerts": True,
        }
    return prefs


@api_router.put("/preferences")
async def update_preferences(req: PreferencesUpdate, current_user: dict = Depends(get_current_user)):
    """Update notification preferences."""
    update_data: Dict[str, Any] = {"updated_at": datetime.utcnow().isoformat()}
    for field in [
        "push_enabled",
        "email_enabled",
        "sms_enabled",
        "ride_updates",
        "promotions",
        "safety_alerts",
    ]:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val

    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("notification_preferences", {"user_id": current_user["id"]}, limit=1)
    )
    if existing:
        await db_supabase.update_one("notification_preferences", {"user_id": current_user["id"]}, update_data)
    else:
        update_data["id"] = str(uuid.uuid4())
        update_data["user_id"] = current_user["id"]
        await db_supabase.insert_one("notification_preferences", update_data)

    return {"success": True}


# ============ Helper function for sending notifications ============


async def create_notification(
    user_id: str,
    title: str,
    body: str,
    notification_type: str = "general",
    data: Optional[Dict[str, Any]] = None,
):
    """Create and optionally push a notification to a user."""
    notification = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": title,
        "body": body,
        "type": notification_type,
        "data": data or {},
        "is_read": False,
        "created_at": datetime.utcnow().isoformat(),
    }
    await db_supabase.insert_one("notifications", notification)
    return notification
