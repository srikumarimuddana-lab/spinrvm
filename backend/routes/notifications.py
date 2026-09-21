"""
notifications.py – In-app notification system for Spinr.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
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

# Holds strong references to in-flight fire-and-forget WS-push tasks so the
# event loop can't garbage-collect one mid-run (a documented asyncio gotcha —
# a Task with no other reference may be collected before it completes).
# Self-cleaning via the done_callback below.
_background_ws_tasks: set = set()

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
        raise HTTPException(status_code=400, detail="We couldn't tell which user to send this to.")

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


# Same invariant routes/rides/matching.py's live offer path enforces via its
# own _FCM_EXCLUDE: no rider name or precise lat/lng may ride in an FCM data
# payload (cleartext in the device tray, transits Google/Apple push infra).
# Kept as a local, debug-endpoint-scoped set rather than importing
# matching.py's — that set is a local variable inside a live dispatch code
# path, not a shared constant, and this fix should not touch that file.
_DEBUG_FCM_EXCLUDE = {
    "rider_name",
    "pickup_lat",
    "pickup_lng",
    "dropoff_lat",
    "dropoff_lng",
}


def _stringify_fcm(payload: Dict[str, Any], exclude: Optional[set] = None) -> Dict[str, str]:
    """Coerce a dispatch payload into FCM's string-only data map.

    Mirrors the stringification the live ride-offer path applies in
    routes/rides/matching.py: dicts/lists become JSON, scalars become str,
    None → "". ``exclude`` drops keys before stringifying, same purpose as
    matching.py's own _FCM_EXCLUDE.
    """
    exclude = exclude or set()
    return {
        k: json.dumps(v) if isinstance(v, (dict, list)) else (str(v) if v is not None else "")
        for k, v in payload.items()
        if k not in exclude
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
    # uses in routes/rides/matching.py. rider_name and precise lat/lng below
    # are stripped before the FCM send by _DEBUG_FCM_EXCLUDE, mirroring that
    # file's own _FCM_EXCLUDE (see 2026-09-19 spinr-notification-ux-reviewer
    # finding — this endpoint previously sent them raw despite this comment
    # already claiming they were excluded).
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
    fcm_data = _stringify_fcm(offer_payload, exclude=_DEBUG_FCM_EXCLUDE)

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


# Values core/middleware.py's ForcedUpgradeMiddleware already recognises on the
# X-App-Platform header, which both apps send on every request via the shared
# client's setAppIdentity() (shared/api/client.ts). Reusing that header is what
# lets the inbox be scoped per app without an app-side change or a new param.
_APP_SURFACES = ("rider", "driver")


def _audience_filter(app_platform: Optional[str]) -> Optional[Dict[str, Any]]:
    """Inbox scoping for a dual-role user, or None to leave the query unscoped.

    A single phone number resolves to one ``users`` row carrying both
    is_rider and is_driver (routes/auth.py reuses the row found by
    get_user_by_phone on OTP verify), so a user_id-only inbox query returns
    the driver's notifications to the rider app and vice versa. Narrowing to
    ``audience IN (<this app>, 'both')`` fixes that while keeping
    account-level notices (audience='both' — suspension, reactivation) in
    both apps, which is where they belong.

    Returns None for a missing or unrecognised header rather than guessing a
    surface. That is the backward-compatible path and it matters: an
    installed build that predates setAppIdentity(), an admin tool, or a
    support engineer with curl must keep seeing the full inbox instead of
    silently losing half of it. Same soft-fail-open posture the
    ForcedUpgradeMiddleware takes on this exact header.
    """
    if app_platform not in _APP_SURFACES:
        return None
    return {"audience": {"$in": [app_platform, "both"]}}


@api_router.get("")
async def get_notifications(
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    x_app_platform: Optional[str] = Header(None),
    current_user: dict = Depends(get_current_user),
):
    """Get user's notifications (paginated), scoped to the requesting app."""
    audience = _audience_filter(x_app_platform)

    filters: Dict[str, Any] = {"user_id": current_user["id"]}
    if audience:
        filters.update(audience)
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

    # Count unread — must carry the same audience scope as the list above, or
    # the bell badge counts notifications this app will never show, which is
    # the dual-role duplicate bug wearing a different hat.
    unread_count = 0
    unread_filters: Dict[str, Any] = {"user_id": current_user["id"], "is_read": False}
    if audience:
        unread_filters.update(audience)
    try:
        unread_count = await db_supabase.count_documents("notifications", unread_filters)
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
async def mark_all_read(
    x_app_platform: Optional[str] = Header(None),
    current_user: dict = Depends(get_current_user),
):
    """Mark this app's notifications as read for the current user.

    Scoped by audience for the same reason the listing is (see
    _audience_filter): for a dual-role user, "mark all read" tapped in the
    rider app must not silently clear the driver app's unread badge for
    notifications the rider app never displayed. Unscoped — unrecognised or
    absent header — keeps the old whole-inbox behavior.
    """
    filters: Dict[str, Any] = {"user_id": current_user["id"], "is_read": False}
    audience = _audience_filter(x_app_platform)
    if audience:
        filters.update(audience)
    await db_supabase.update_one(
        "notifications",
        filters,
        {"is_read": True, "read_at": datetime.now(timezone.utc).isoformat()},
    )
    return {"success": True}


@api_router.delete("/{notification_id}")
async def delete_notification(notification_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a single notification owned by the requesting user.

    Ownership check mirrors mark_as_read/mark_all_read above: every filter
    is scoped to {"id": ..., "user_id": current_user["id"]} so a caller can
    never delete someone else's row. 404 (not a silent no-op) when the id
    doesn't exist or isn't owned by this user, so the client can tell "gone"
    apart from "not yours" without leaking which case it was.
    """
    existing = await db_supabase.get_rows(
        "notifications",
        {"id": notification_id, "user_id": current_user["id"]},
        limit=1,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db_supabase.delete_many(
        "notifications",
        {"id": notification_id, "user_id": current_user["id"]},
    )
    return {"success": True}


@api_router.delete("")
async def clear_notifications(
    read_only: bool = Query(False),
    x_app_platform: Optional[str] = Header(None),
    current_user: dict = Depends(get_current_user),
):
    """Clear notifications for the requesting user.

    Default (no ``read_only``) clears every notification this app shows.
    ``?read_only=true`` clears only already-read ones, so the app can offer
    both a non-destructive "clear read" and a destructive "clear all" from
    one endpoint. Always scoped to ``user_id == current_user["id"]`` — a
    single filtered DELETE, no loop over rows (no N+1 here).

    Also scoped by audience (see _audience_filter). This one is the reason
    that matters most: without it, a dual-role driver tapping "Clear all" in
    the driver app permanently deletes their rider ride receipts and refund
    notices too — rows they never saw in this app and cannot get back. The
    scope only ever narrows what a DELETE touches, so it is strictly safer
    than the unscoped behavior it replaces.
    """
    filters: Dict[str, Any] = {"user_id": current_user["id"]}
    audience = _audience_filter(x_app_platform)
    if audience:
        filters.update(audience)
    if read_only:
        filters["is_read"] = True
    await db_supabase.delete_many("notifications", filters)
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
    # Fire-and-forget: create_notification() is called synchronously from
    # many request-handling paths across the codebase, some latency-
    # sensitive (see CLAUDE.md's anti-pattern note on awaiting a slow
    # side-effect inline in a request handler). Scheduling this as a task
    # instead of awaiting it means a slow/stuck socket send can never add
    # latency to the caller — the notification DB row (the durable,
    # required side effect) is already committed by the time this line
    # runs either way.
    ws_task = asyncio.create_task(_emit_new_notification_ws(user_id, notification))
    _background_ws_tasks.add(ws_task)
    ws_task.add_done_callback(_background_ws_tasks.discard)
    return notification


async def _emit_new_notification_ws(user_id: str, notification: Dict[str, Any]) -> None:
    """Best-effort WS push for a freshly-created notification.

    The DB row above is the durable side effect and is already written by
    the time this runs — a WS-send failure (or the very common case of the
    recipient simply not being connected right now) must never surface as
    an error from create_notification, since callers throughout the
    codebase treat it as a fire-and-forget DB write.

    The connection registry keys on client type ("rider_{user_id}" /
    "driver_{user_id}", see CLAUDE.md's WebSocket-auth convention) and this
    helper has no reliable signal for which app surface the recipient is
    using, so it best-effort-tries both keys via the same
    ``manager.send_personal_message`` targeted-send path the ride-event
    code uses (``socket_manager.ConnectionManager.broadcast_ride_status``) —
    not a new transport. ``send_personal_message`` and everything it
    delegates to (local delivery, Redis pub/sub) already catch and log
    their own failures internally and never raise, so the try/except below
    is defensive belt-and-suspenders against a future change to that
    contract, not a path that currently ever triggers.
    """
    try:
        from ..socket_manager import manager
    except ImportError:  # pragma: no cover
        from socket_manager import manager  # type: ignore

    try:
        unread_count = await db_supabase.count_documents("notifications", {"user_id": user_id, "is_read": False})
    except Exception as e:
        # Best-effort push on an already best-effort path — fall back to a
        # non-zero placeholder rather than skip the push over a count error.
        logger.debug(f"new_notification unread_count fallback for user {user_id}: {e}")
        unread_count = 1

    ws_payload = {
        "type": "new_notification",
        "notification": notification,
        "unread_count": unread_count,
    }
    for client_type in ("rider", "driver"):
        client_id = f"{client_type}_{user_id}"
        try:
            await manager.send_personal_message(ws_payload, client_id)
        except Exception as e:
            # Debug, not error/warning: an offline recipient is the expected
            # common case for a WS push, not a fault to surface loudly.
            logger.debug(f"new_notification WS push skipped for {client_id}: {e}")
