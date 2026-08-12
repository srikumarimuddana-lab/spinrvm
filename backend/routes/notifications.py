"""
notifications.py – In-app notification system for Spinr.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from .. import db_supabase
    from ..dependencies import get_admin_user, get_current_user
    from ..features import send_push_notification
except ImportError:
    import db_supabase  # type: ignore
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
    earnings_summary: Optional[bool] = None


class RegisterTokenRequest(BaseModel):
    token: str
    platform: str = "unknown"
    client_type: Optional[str] = None


class TestPushRequest(BaseModel):
    user_id: Optional[str] = None  # None → send to the admin's own account
    title: str = "Spinr test push"
    body: str = "If you can see this, push notifications are wired up correctly."


class DebugRideOfferRequest(BaseModel):
    user_id: str  # target driver's user id — debugging a specific device
    pickup_address: str = "Test pickup — 8th St E, Saskatoon"
    dropoff_address: str = "Test dropoff — Stonebridge, Saskatoon"
    fare: float = 12.50
    countdown_seconds: int = 30


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


def _stringify_fcm(payload: Dict[str, Any]) -> Dict[str, str]:
    """Coerce a dispatch payload into FCM's string-only data map.

    Mirrors the stringification the live ride-offer path applies in
    routes/rides.py: dicts/lists become JSON, scalars become str, None → "".
    """
    return {
        k: json.dumps(v) if isinstance(v, (dict, list)) else (str(v) if v is not None else "")
        for k, v in payload.items()
    }


@api_router.post("/debug-ride-offer")
async def admin_debug_ride_offer(body: DebugRideOfferRequest, admin: dict = Depends(get_admin_user)):
    """Simulate a real ride-offer push end-to-end against a driver's device.

    Unlike /test-push (a plain notification on the generic ``fcm_token``), this
    reproduces the EXACT path a live ride offer takes:

      * reads the driver-specific token (``users.fcm_token_driver`` → falls
        back to ``fcm_token``), the same column dispatch reads via
        ``target_app="driver"``;
      * sends a data-only ``new_ride_assignment`` FCM message at
        ``priority="dispatch"`` so the driver app's headless handler + Notifee
        render the full-screen offer with the ride-offer sound, and iOS gets
        the ``ride_offer.caf`` APNs alert;
      * returns which token column was used, masked previews of all three
        token columns, and whether Firebase accepted the send.

    Use when a driver reports "no offer push / no sound". The response isolates
    the failure stage:
      * ``no_driver_token`` → the driver app never registered a token (token
        problem, not a build problem);
      * ``success=true`` but device shows nothing → the installed binary lacks
        the merged native code (needs an EAS rebuild — OTA can't ship native
        changes) or OS notifications are disabled;
      * ``success=false`` → Firebase rejected the send (stale token purged, or
        FIREBASE_SERVICE_ACCOUNT_JSON misconfigured — check server logs).

    No real ride is dispatched; the synthetic ``ride_id`` is a ``debug-`` value,
    so Accept/Decline from the device resolve to a harmless 404.
    """
    user = await db.find_one("users", {"id": body.user_id})
    if not user:
        raise HTTPException(status_code=404, detail=f"User {body.user_id} not found")

    driver_token = user.get("fcm_token_driver")
    rider_token = user.get("fcm_token_rider")
    generic_token = user.get("fcm_token")

    # Mirror send_push_notification(target_app="driver") token selection so the
    # response reports the exact column the dispatch send will read.
    used_token = driver_token or generic_token
    used_column = "fcm_token_driver" if driver_token else ("fcm_token" if generic_token else None)

    token_previews = {
        "fcm_token_driver": _mask_token(driver_token),
        "fcm_token_rider": _mask_token(rider_token),
        "fcm_token": _mask_token(generic_token),
    }

    if not used_token:
        return {
            "success": False,
            "reason": "no_driver_token",
            "detail": (
                "No fcm_token_driver or fcm_token on file. The driver app has "
                "not registered a push token for this account. Re-open the "
                "driver app, grant the notification permission, and confirm "
                "POST /notifications/register-token ran with client_type=driver."
            ),
            "target_user_id": body.user_id,
            "tokens": token_previews,
            "is_online": user.get("is_online"),
            "is_driver": user.get("is_driver"),
        }

    routing = "expo" if used_token.startswith(("ExponentPushToken", "ExpoPushToken")) else "fcm"

    ride_id = f"debug-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    offer_expires_at = (now + timedelta(seconds=body.countdown_seconds)).isoformat()

    # Minimal but realistic dispatch payload — same shape/keys the live offer
    # uses in routes/rides.py (spatial fields are excluded there too).
    offer_payload = {
        "type": "new_ride_assignment",
        "ride_id": ride_id,
        "booking_id": ride_id,
        "pickup_address": body.pickup_address,
        "dropoff_address": body.dropoff_address,
        "pickup_lat": 52.1332,
        "pickup_lng": -106.6700,
        "dropoff_lat": 52.1100,
        "dropoff_lng": -106.6300,
        "fare": body.fare,
        "distance_km": 4.2,
        "duration_minutes": 11,
        "rider_name": "Debug Rider",
        "requires_wav": False,
        "quiet_mode": False,
        "countdown_seconds": body.countdown_seconds,
        "offer_expires_at": offer_expires_at,
        "deeplink": "/driver/",
    }
    fcm_data = _stringify_fcm(offer_payload)

    earnings_label = f"${body.fare:.2f}"
    delivered = await send_push_notification(
        body.user_id,
        f"{earnings_label} ride offer",
        f"Booking {ride_id} • {body.pickup_address} → {body.dropoff_address}",
        fcm_data,
        priority="dispatch",
        target_app="driver",
    )

    return {
        "success": bool(delivered),
        "target_user_id": body.user_id,
        "token_column_used": used_column,
        "routing": routing,
        "ride_id": ride_id,
        "tokens": token_previews,
        "is_online": user.get("is_online"),
        "is_driver": user.get("is_driver"),
        "note": (
            "success=true means Firebase accepted the message for delivery. If "
            "the device still shows no offer or plays no sound, the installed "
            "binary lacks the merged native code (rebuild via EAS — an OTA "
            "update cannot ship native changes) or OS notifications are off. A "
            "data-only dispatch renders only through the app's Notifee handler, "
            "so an outdated build shows nothing even on a successful send."
        ),
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
    client_type = body.client_type if body.client_type in ("rider", "driver") else None

    # If the app didn't send client_type (pre-EAS-update), infer from
    # the user's is_driver flag. A pure rider (is_driver=false) must be
    # the rider app; a driver-only user must be the driver app.
    # Dual-role users stay None until the EAS update ships client_type.
    if not client_type:
        is_driver = current_user.get("is_driver", False)
        is_rider = current_user.get("is_rider", True)
        if is_driver and not is_rider:
            client_type = "driver"
        elif is_rider and not is_driver:
            client_type = "rider"
        else:
            # Dual-role: write to BOTH columns so cloud messaging
            # reaches whichever app surface the admin targets.
            client_type = "both"

    # Upsert: one token per user per platform.
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

    user_update: dict = {"fcm_token": token}
    if client_type == "driver":
        user_update["fcm_token_driver"] = token
    elif client_type == "rider":
        user_update["fcm_token_rider"] = token
    elif client_type == "both":
        user_update["fcm_token_rider"] = token
        user_update["fcm_token_driver"] = token

    try:
        await db.update_one("users", {"id": current_user["id"]}, user_update)
    except Exception as exc:
        logger.error(f"Failed to mirror FCM token onto users: {exc}", exc_info=True)

    logger.info(f"FCM token registered for user {current_user['id']} ({platform}, inferred={client_type})")
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


async def _global_throttle_info() -> Dict[str, Any]:
    """Read-only quiet-hours/cap info for a future settings screen.

    Deliberately NOT per-user: notification_throttling_enabled and its
    quiet-hours/cap values are global admin-controlled settings (migration
    304, editable only via PUT /admin/settings), not columns on this user's
    notification_preferences row. There is no per-user override yet — see
    migration 304's own comment for why that's a deliberate V1 scope
    decision, not an oversight. This just lets a rider/driver-facing
    settings screen display "Quiet hours: 10 PM-7 AM" (when throttling is
    on) without implying the user can change it from here.
    """
    try:
        from ..settings_loader import get_app_settings
    except ImportError:  # pragma: no cover
        from settings_loader import get_app_settings  # type: ignore
    settings = await get_app_settings()
    return {
        "notification_throttling_enabled": bool(settings.get("notification_throttling_enabled")),
        "notification_quiet_hours_start": settings.get("notification_quiet_hours_start") or "22:00",
        "notification_quiet_hours_end": settings.get("notification_quiet_hours_end") or "07:00",
        "notification_daily_cap": int(settings.get("notification_daily_cap") or 0),
    }


@api_router.get("/preferences")
async def get_preferences(current_user: dict = Depends(get_current_user)):
    """Get user's notification preferences, plus read-only global
    throttling info (see _global_throttle_info)."""
    prefs = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("notification_preferences", {"user_id": current_user["id"]}, limit=1)
    )
    throttle_info = await _global_throttle_info()
    if not prefs:
        # Return defaults
        return {
            "push_enabled": True,
            "email_enabled": True,
            "sms_enabled": False,
            "ride_updates": True,
            "promotions": True,
            "safety_alerts": True,
            "earnings_summary": True,
            **throttle_info,
        }
    return {**prefs, **throttle_info}


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
        "earnings_summary",
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
