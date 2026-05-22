"""Push notification retry queue backed by the push_retry_queue table.

Dispatch and safety pushes are enqueued here instead of fired directly so
that a transient FCM/Expo outage doesn't silently drop a ride offer or SOS
alert.  A background loop picks up unsent rows every 30 seconds, attempts
delivery, and applies exponential back-off before giving up after
_MAX_ATTEMPTS failures.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger

try:
    from ..db_supabase import run_sync
    from ..features import _is_expo_token, _send_expo_push
    from ..supabase_client import supabase
except ImportError:
    from db_supabase import run_sync  # type: ignore
    from features import _is_expo_token, _send_expo_push  # type: ignore
    from supabase_client import supabase  # type: ignore

_LOOP_INTERVAL = 30  # seconds between ticks
_MAX_ATTEMPTS = 5  # give up after this many failures
_BACKOFF_BASE = 60  # seconds; doubled for each subsequent attempt


async def enqueue_push(
    user_id: str,
    title: str,
    body: str,
    data: dict | None = None,
    priority: str = "normal",
) -> None:
    """Write a push notification to the retry queue (best-effort).

    Failures are logged but not re-raised — a broken enqueue must not crash
    the caller's request path.
    """
    try:
        await run_sync(
            lambda: (
                supabase.table("push_retry_queue")
                .insert(
                    {
                        "user_id": user_id,
                        "title": title,
                        "body": body,
                        "data": data or {},
                        "priority": priority,
                    }
                )
                .execute()
            )
        )
        logger.info(f"push_retry: enqueued {priority} push for user {user_id!r}")
    except Exception:
        logger.error(
            f"push_retry: failed to enqueue push for user {user_id!r}",
            exc_info=True,
        )


async def push_retry_loop() -> None:
    """Background loop: attempt delivery of queued push notifications every 30s."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.error("push_retry_loop tick failed", exc_info=True)
        await asyncio.sleep(_LOOP_INTERVAL)


async def _tick() -> None:
    """Process one batch of due, unsent push notifications."""
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        resp = await run_sync(
            lambda: (
                supabase.table("push_retry_queue")
                .select("*, users!inner(fcm_token)")
                .is_("sent_at", "null")
                .lte("next_attempt_at", now_iso)
                .lte("attempts", _MAX_ATTEMPTS - 1)
                .limit(50)
                .execute()
            )
        )
    except Exception:
        logger.error("push_retry: failed to query pending rows", exc_info=True)
        return

    rows = resp.data or []
    if not rows:
        return

    logger.info(f"push_retry: processing {len(rows)} pending notification(s)")

    for row in rows:
        await _process_row(row)


async def _process_row(row: dict) -> None:
    """Attempt delivery for a single queued push notification row."""
    row_id: str = row["id"]
    user_id: str = row["user_id"]
    title: str = row["title"]
    body: str = row["body"]
    data: dict = row.get("data") or {}
    attempts: int = row["attempts"]

    # The join produces a nested dict under the "users" key.
    user_data = row.get("users") or {}
    token: str | None = user_data.get("fcm_token") if isinstance(user_data, dict) else None

    if not token:
        logger.info(f"push_retry: no FCM token for user {user_id!r}, dropping row {row_id}")
        await _delete_row(row_id)
        return

    success = False
    try:
        if _is_expo_token(token):
            success = await _send_expo_push(token, title, body, data)
        else:
            success = await _send_fcm_push(token, title, body, data, user_id)
    except Exception:
        logger.error(
            f"push_retry: unexpected error sending to user {user_id!r} (row {row_id})",
            exc_info=True,
        )
        success = False

    now_iso = datetime.now(timezone.utc).isoformat()

    if success:
        try:
            await run_sync(
                lambda: supabase.table("push_retry_queue").update({"sent_at": now_iso}).eq("id", row_id).execute()
            )
            logger.info(
                f"push_retry: delivered notification to user {user_id!r} (row {row_id}, attempt {attempts + 1})"
            )
        except Exception:
            logger.error(
                f"push_retry: failed to mark row {row_id} as sent",
                exc_info=True,
            )
        return

    # Delivery failed — apply back-off or drop if exhausted.
    new_attempts = attempts + 1
    if new_attempts >= _MAX_ATTEMPTS:
        logger.error(
            f"push_retry: giving up on row {row_id} for user {user_id!r} after {new_attempts} attempt(s); dropping"
        )
        await _delete_row(row_id)
        return

    backoff_seconds = _BACKOFF_BASE * (2**attempts)
    next_attempt_at = (datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)).isoformat()
    try:
        await run_sync(
            lambda: (
                supabase.table("push_retry_queue")
                .update(
                    {
                        "attempts": new_attempts,
                        "next_attempt_at": next_attempt_at,
                    }
                )
                .eq("id", row_id)
                .execute()
            )
        )
        logger.warning(f"push_retry: attempt {new_attempts} failed for row {row_id}; next retry in {backoff_seconds}s")
    except Exception:
        logger.error(
            f"push_retry: failed to update back-off for row {row_id}",
            exc_info=True,
        )


async def _send_fcm_push(
    token: str,
    title: str,
    body: str,
    data: dict,
    user_id: str,
) -> bool:
    """Send a push notification via Firebase Admin SDK (native FCM token).

    Dispatch pushes (data["type"] == "new_ride_assignment") are sent
    Android-data-only so the OS does NOT show its default banner — the
    driver app's Notifee background handler picks up the data payload
    and displays a rich heads-up + full-screen-intent notification with
    Accept/Decline action buttons. Without this branch, drivers see two
    competing notifications (the OS one and the Notifee one).
    """
    try:
        from firebase_admin import messaging
    except ImportError:
        logger.warning("push_retry: firebase_admin not available; cannot deliver FCM push")
        return False

    is_dispatch = (data or {}).get("type") == "new_ride_assignment"

    try:
        # Android: data-only for dispatch (Notifee renders). For everything
        # else, keep the default notification block so the OS handles it.
        android_cfg = messaging.AndroidConfig(
            priority="high",
            notification=None
            if is_dispatch
            else messaging.AndroidNotification(
                channel_id="ride-offers",
            ),
        )
        message = messaging.Message(
            # Top-level notification only when iOS needs an alert.
            notification=None if is_dispatch else messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=token,
            android=android_cfg,
            apns=messaging.APNSConfig(
                headers={
                    "apns-priority": "10",
                    "apns-push-type": "alert",
                },
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(title=title, body=body),
                        sound="ride_offer.caf" if is_dispatch else "default",
                        category="ride-offer" if is_dispatch else None,
                        content_available=True,
                        mutable_content=True,
                    ),
                )
                if is_dispatch
                else None,
            ),
        )
        response = await asyncio.to_thread(messaging.send, message)
        logger.info(f"push_retry: FCM send OK for user {user_id!r}: {response} (dispatch={is_dispatch})")
        return True
    except Exception:
        logger.error(
            f"push_retry: FCM send failed for user {user_id!r}",
            exc_info=True,
        )
        return False


async def _delete_row(row_id: str) -> None:
    """Remove a permanently-failed push notification row."""
    try:
        await run_sync(lambda: supabase.table("push_retry_queue").delete().eq("id", row_id).execute())
    except Exception:
        logger.error(f"push_retry: failed to delete row {row_id}", exc_info=True)
