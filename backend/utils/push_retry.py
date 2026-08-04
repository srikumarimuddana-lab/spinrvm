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
    target_app: str | None = None,
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
                        "target_app": target_app,
                    }
                )
                .execute()
            )
        )
        logger.info(f"push_retry: enqueued {priority} push for user {user_id!r}")
    except Exception:
        logger.opt(exception=True).error(f"push_retry: failed to enqueue push for user {user_id!r}")


async def push_retry_loop() -> None:
    """Background loop: attempt delivery of queued push notifications every 30s."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.opt(exception=True).error("push_retry_loop tick failed")
        await asyncio.sleep(_LOOP_INTERVAL)


async def _tick() -> None:
    """Process one batch of due, unsent push notifications."""
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        resp = await run_sync(
            lambda: (
                supabase.table("push_retry_queue")
                .select(
                    "id,user_id,title,body,data,attempts,target_app,"
                    " users!inner(fcm_token,fcm_token_rider,fcm_token_driver)"
                )
                .is_("sent_at", "null")
                .lte("next_attempt_at", now_iso)
                .lte("attempts", _MAX_ATTEMPTS - 1)
                .limit(50)
                .execute()
            )
        )
    except Exception:
        logger.opt(exception=True).error("push_retry: failed to query pending rows")
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
    target_app: str | None = row.get("target_app")

    # The join produces a nested dict under the "users" key. Prefer the
    # app-specific token for queued pushes so a dual-role user's driver ride
    # offer does not get sent to their rider app token (or vice versa).
    user_data = row.get("users") or {}
    token: str | None = None
    if isinstance(user_data, dict):
        if target_app == "driver":
            token = user_data.get("fcm_token_driver") or user_data.get("fcm_token")
        elif target_app == "rider":
            token = user_data.get("fcm_token_rider") or user_data.get("fcm_token")
        else:
            token = user_data.get("fcm_token")

    if not token:
        logger.info(f"push_retry: no FCM token for user {user_id!r} (target_app={target_app!r}), dropping row {row_id}")
        await _delete_row(row_id)
        return

    # Replay-safety (F5): atomically lease this row BEFORE sending so two
    # replicas can't both deliver it. The conditional update bumps attempts
    # (compare-and-swap on the observed value) and sets the back-off window in
    # one statement; a racing replica that already claimed the row gets zero
    # rows back here and skips. Writing next_attempt_at at claim time also acts
    # as a crash-safe lease: if this worker dies after claiming but before
    # delivering, the row becomes due again after the back-off rather than lost.
    backoff_seconds = _BACKOFF_BASE * (2**attempts)
    next_attempt_at = (datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)).isoformat()
    if not await _claim_row(row_id, attempts, next_attempt_at):
        logger.info(f"push_retry: row {row_id} already claimed by another worker; skipping")
        return

    success = False
    try:
        if _is_expo_token(token):
            success = await _send_expo_push(token, title, body, data)
        else:
            success = await _send_fcm_push(token, title, body, data, user_id)
    except Exception:
        logger.opt(exception=True).error(f"push_retry: unexpected error sending to user {user_id!r} (row {row_id})")
        success = False

    if success:
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            await run_sync(
                lambda: supabase.table("push_retry_queue").update({"sent_at": now_iso}).eq("id", row_id).execute()
            )
            logger.info(
                f"push_retry: delivered notification to user {user_id!r} (row {row_id}, attempt {attempts + 1})"
            )
        except Exception:
            logger.opt(exception=True).error(f"push_retry: failed to mark row {row_id} as sent")
        return

    # Delivery failed. The atomic claim already incremented attempts and
    # scheduled the next retry via next_attempt_at; here we only drop the row
    # once attempts are exhausted.
    new_attempts = attempts + 1
    if new_attempts >= _MAX_ATTEMPTS:
        logger.error(
            f"push_retry: giving up on row {row_id} for user {user_id!r} after {new_attempts} attempt(s); dropping"
        )
        await _delete_row(row_id)
        return

    logger.warning(f"push_retry: attempt {new_attempts} failed for row {row_id}; next retry in {backoff_seconds}s")


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
        logger.opt(exception=True).error(f"push_retry: FCM send failed for user {user_id!r}")
        return False


async def _claim_row(row_id: str, observed_attempts: int, next_attempt_at: str) -> bool:
    """Atomically lease one queue row for delivery (replay-safety, F5).

    Compare-and-swap on the observed ``attempts`` value while ``sent_at IS
    NULL``: only the worker whose UPDATE flips attempts wins the row;
    concurrent workers on other replicas read the same snapshot but match zero
    rows once attempts has advanced, so they skip. Returns True iff this worker
    claimed the row. Never raises — a claim failure is treated as "not claimed".
    """

    def _fn() -> bool:
        res = (
            supabase.table("push_retry_queue")
            .update({"attempts": observed_attempts + 1, "next_attempt_at": next_attempt_at})
            .eq("id", row_id)
            .eq("attempts", observed_attempts)
            .is_("sent_at", "null")
            .execute()
        )
        return bool(getattr(res, "data", None))

    try:
        return await run_sync(_fn)
    except Exception:
        logger.opt(exception=True).error(f"push_retry: failed to claim row {row_id}")
        return False


async def _delete_row(row_id: str) -> None:
    """Remove a permanently-failed push notification row."""
    try:
        await run_sync(lambda: supabase.table("push_retry_queue").delete().eq("id", row_id).execute())
    except Exception:
        logger.opt(exception=True).error(f"push_retry: failed to delete row {row_id}")
