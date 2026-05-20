"""
notifications.py – In-app notification system for Spinr.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from .. import db_supabase
    from ..dependencies import get_admin_user, get_current_user
    from ..features import send_push_notification
except ImportError:
    import db_supabase
    from dependencies import get_admin_user, get_current_user  # type: ignore
    from features import send_push_notification  # type: ignore

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


class TestPushRequest(BaseModel):
    user_id: Optional[str] = None  # None → send to the admin's own account
    title: str = "Spinr test push"
    body: str = "If you can see this, push notifications are wired up correctly."


def _mask_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    if len(token) <= 12:
        return token[:4] + "..."
    return f"{token[:8]}...{token[-4:]}"


@api_router.post("/test-push")
async def admin_send_test_push(body: TestPushRequest, admin: dict = Depends(get_admin_user)):
    """Send a manual push to verify the end-to-end pipeline is wired up.

    Admin-only. Use this when:
      * You've just set FIREBASE_SERVICE_ACCOUNT_JSON and want to confirm
        firebase_admin can sign and dispatch a message.
      * A rider/driver reports "I'm not getting notifications" — call this
        against their user_id; the response shows whether a token is on
        file and the result of the send.

    Returns:
      success: bool — True when firebase_admin returned a message id (or
        the Expo REST API returned 200 for an ExponentPushToken).
      token_on_file: bool — whether users.fcm_token has a value.
      token_preview: masked token (first 8 + last 4) for sanity-check
        against the device's "Show FCM token" debug view.
      platform_hint: 'expo' | 'fcm' | None — how send_push_notification
        will route the message.
    """
    target_user_id = body.user_id or admin.get("id")
    if not target_user_id:
        raise HTTPException(status_code=400, detail="No user_id and admin has no id claim")

    user = await db.find_one("users", {"id": target_user_id})
    if not user:
        raise HTTPException(status_code=404, detail=f"User {target_user_id} not found")

    token = user.get("fcm_token")
    platform_hint = None
    if token:
        platform_hint = "expo" if token.startswith(("ExponentPushToken", "ExpoPushToken")) else "fcm"

    ok = await send_push_notification(
        target_user_id,
        body.title,
        body.body,
        data={"type": "test_push"},
    )

    return {
        "success": bool(ok),
        "target_user_id": target_user_id,
        "token_on_file": bool(token),
        "token_preview": _mask_token(token),
        "platform_hint": platform_hint,
    }


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
            "push_tokens",
            {"id": existing["id"]},
            {"token": token, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
    else:
        await db_supabase.insert_one(
            "push_tokens",
            {
                "id": str(uuid.uuid4()),
                "user_id": current_user["id"],
                "token": token,
                "platform": platform,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    # Mirror to users.fcm_token so features.send_push_notification can find it.
    # This is best-effort: if the column doesn't exist yet (pre sql/03_features),
    # the update is a no-op rather than a hard failure.
    try:
        await db.update_one("users", {"id": current_user["id"]}, {"$set": {"fcm_token": token}})
    except Exception as exc:
        logger.error(f"Failed to mirror FCM token onto users.fcm_token: {exc}", exc_info=True)

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
        logger.warning(
            "list_notifications: failed to fetch unread_count for user %s",
            current_user.get("id"),
            exc_info=True,
        )

    return {"notifications": notifications, "unread_count": unread_count}


@api_router.put("/{notification_id}/read")
async def mark_as_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    """Mark a single notification as read."""
    await db_supabase.update_one(
        "notifications",
        {"id": notification_id, "user_id": current_user["id"]},
        {"is_read": True, "read_at": datetime.now(timezone.utc).isoformat()},
    )
    return {"success": True}


@api_router.put("/read-all")
async def mark_all_read(current_user: dict = Depends(get_current_user)):
    """Mark all notifications as read for the current user."""
    await db_supabase.update_one(
        "notifications",
        {"user_id": current_user["id"], "is_read": False},
        {"is_read": True, "read_at": datetime.now(timezone.utc).isoformat()},
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
    update_data: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
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

# Deeplink routes for each notification type (13-3)
NOTIFICATION_DEEPLINKS: Dict[str, str] = {
    "ride_offer": "/driver/",
    "new_ride_offer": "/driver/",
    "document_expiry": "/driver/documents",
    "document_expiry_warning": "/driver/documents",
    "document_expiry_1day": "/driver/documents",
    "document_expiry_today": "/driver/documents",
    "payout_processed": "/driver/earnings",
    "payout_failed": "/driver/earnings",
    "quest_earned": "/driver/quests",
    "subscription_expiry": "/driver/subscription",
    "subscription_expiring": "/driver/subscription",
}


async def create_notification(
    user_id: str,
    title: str,
    body: str,
    notification_type: str = "general",
    data: Optional[Dict[str, Any]] = None,
):
    """Create and optionally push a notification to a user."""
    payload = dict(data or {})
    # Inject deeplink so the app can navigate on tap (13-3)
    if "deeplink" not in payload and notification_type in NOTIFICATION_DEEPLINKS:
        payload["deeplink"] = NOTIFICATION_DEEPLINKS[notification_type]
    notification = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": title,
        "body": body,
        "type": notification_type,
        "data": payload,
        "is_read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("notifications", notification)
    return notification
