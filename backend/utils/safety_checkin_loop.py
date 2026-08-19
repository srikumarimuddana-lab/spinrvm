"""Safety check-in loop (Feature D — P3).

For rides that have been ``in_progress`` for ≥ 20 minutes, send the rider a
silent FCM push asking "Are you okay?".  If the rider does not tap the
in-app confirmation within 90 seconds, the loop escalates by inserting an
open safety incident so the trust-and-safety team can follow up.

State is tracked in Redis to avoid re-sending or re-escalating:
  ``safety:checkin:sent:{ride_id}``   — set when push is sent (TTL: 4 h)
  ``safety:checkin:ok:{ride_id}``     — set by POST /rides/{id}/safety-checkin
  ``safety:checkin:escalated:{ride_id}`` — set after escalation (TTL: 4 h)

Replay-safety: all three Redis keys prevent duplicate actions on replicas
running the same loop concurrently. The ``sent`` key specifically is claimed
via an atomic ``SET ... NX`` (``redis_set_nx``, the same primitive every other
leader-election / dedupe lock in ``backend/utils/`` uses — e.g.
``scheduled_rides.py``'s notify dedupe, ``routes/rides/payments.py``'s wallet
re-drive lock) rather than a read-then-write, so two concurrent ticks
(same replica racing itself, or two replicas racing each other) can never
both observe "not sent yet" and both fire the push.

Interval: 30 seconds.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 30
_CHECKIN_AFTER_MINUTES = 20  # send push after this many minutes in_progress
_ESCALATE_AFTER_SECONDS = 90  # escalate if no response within this window

try:
    from .redis_client import redis_delete, redis_get, redis_set, redis_set_nx
except ImportError:
    from utils.redis_client import redis_delete, redis_get, redis_set, redis_set_nx  # type: ignore

try:
    from ..db import db as _supabase_db
    from ..features import notify_safety_team, send_push_notification
    from ..socket_manager import manager as _ws_manager
    from .audit_logger import log_admin_action as _log_audit
except ImportError:
    from db import db as _supabase_db  # type: ignore
    from features import notify_safety_team, send_push_notification  # type: ignore
    from socket_manager import manager as _ws_manager  # type: ignore # noqa: F401
    from utils.audit_logger import log_admin_action as _log_audit  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


# Must match the exact name lifespan.py passes to _spawn() for this loop —
# the watchdog matches on this string.
_LOOP_NAME = "safety_checkin (30s)"


def _sent_key(ride_id: str) -> str:
    return f"safety:checkin:sent:{ride_id}"


def _ok_key(ride_id: str) -> str:
    return f"safety:checkin:ok:{ride_id}"


def _escalated_key(ride_id: str) -> str:
    return f"safety:checkin:escalated:{ride_id}"


async def safety_checkin_loop() -> None:
    """Periodic check-in loop — runs on every replica; Redis keys provide idempotency."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.error("safety_checkin_loop tick failed", exc_info=True)
        _record_heartbeat(_LOOP_NAME)
        await asyncio.sleep(_INTERVAL_SECONDS)


async def _tick() -> None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=_CHECKIN_AFTER_MINUTES)

    in_progress = await _supabase_db.get_rows(
        "rides",
        {"status": "in_progress"},
        limit=200,
        columns="id,rider_id,started_at,updated_at",
    )

    for ride in in_progress or []:
        ride_id = ride.get("id")
        if not ride_id:
            continue

        # Only act on rides that started long enough ago.
        started_at_raw = ride.get("ride_started_at") or ride.get("updated_at")
        if not started_at_raw:
            continue
        try:
            started_at = datetime.fromisoformat(started_at_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if started_at > cutoff:
            continue  # ride not yet 20 min old

        # Atomically claim the "sent" slot before doing anything else. A plain
        # read-then-write here (GET is-not-set -> send -> SET) has a TOCTOU
        # race window: two concurrent ticks (this replica racing itself on a
        # slow send, or two replicas racing each other) can both read "not
        # sent" and both fire the push. `redis_set_nx` (SET ... NX) makes the
        # claim atomic — only one caller ever gets `True` for a given
        # ride_id, so only one caller ever proceeds to send. TTL = 4 h so the
        # key expires well after the ride ends and won't accumulate
        # indefinitely.
        claimed = await redis_set_nx(_sent_key(ride_id), now.isoformat(), ttl=4 * 3600)

        if claimed:
            # Won the claim — we're the one caller responsible for sending.
            rider_id = ride.get("rider_id")
            if rider_id:
                try:
                    await send_push_notification(
                        rider_id,
                        "Safety check-in",
                        "Just checking in — are you okay? Tap to confirm.",
                        data={"type": "safety_checkin", "ride_id": ride_id},
                    )
                except Exception:
                    logger.error(
                        f"[SAFETY_CHECKIN] FCM push failed ride_id={ride_id}",
                        exc_info=True,
                    )
                    # Release the claim so a later tick (this replica or
                    # another) can retry the send instead of silently never
                    # notifying this rider.
                    try:
                        await redis_delete(_sent_key(ride_id))
                    except Exception:
                        logger.error(
                            f"[SAFETY_CHECKIN] Failed to release claim after push failure ride_id={ride_id}",
                            exc_info=True,
                        )
                    continue

            logger.info(f"[SAFETY_CHECKIN] Check-in sent for ride {ride_id}")
            continue

        # Did not win the claim — another tick already sent (or is sending)
        # this check-in. This is expected, routine contention, not a
        # failure, so it's logged at debug rather than error/warning.
        logger.debug(f"[SAFETY_CHECKIN] Check-in already claimed for ride {ride_id}; skipping duplicate send")
        sent_ts_str = await redis_get(_sent_key(ride_id))
        if not sent_ts_str:
            # The claim key vanished between the NX attempt and this read
            # (e.g. TTL edge, key was never actually persisted) — treat as
            # not-yet-sent and let the next tick attempt to claim again.
            continue

        # Push was already sent.  Check whether the rider responded.
        ok = await redis_get(_ok_key(ride_id))
        if ok:
            continue  # rider confirmed — nothing to do

        # No response yet; check if we're past the escalation window.
        escalated = await redis_get(_escalated_key(ride_id))
        if escalated:
            continue  # already escalated this check-in cycle

        try:
            sent_at = datetime.fromisoformat(sent_ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        seconds_since_sent = (now - sent_at).total_seconds()
        if seconds_since_sent < _ESCALATE_AFTER_SECONDS:
            continue  # still within the response window

        # Escalate: insert an open safety incident for the ops team.
        try:
            await _escalate(ride, now)
        except Exception:
            # _escalate already logged the error. The _escalated_key is NOT set,
            # so the next tick will retry. Log ride_id here for ops correlation.
            logger.error(
                f"[SAFETY_CHECKIN] Escalation failed for ride {ride_id}; "
                "will retry on next tick. Check DB/Redis connectivity.",
                exc_info=True,
            )


async def _escalate(ride: dict, now: datetime) -> None:
    ride_id = ride.get("id")
    rider_id = ride.get("rider_id")

    try:
        incident = {
            "id": str(uuid.uuid4()),
            "reported_by_user_id": rider_id,
            "role": "rider",
            "category": "safety_checkin_no_response",
            "description": (
                f"Rider did not respond to automatic safety check-in after "
                f"{_ESCALATE_AFTER_SECONDS}s. Ride ID: {ride_id}. "
                "Please follow up via the safety dashboard."
            ),
            "status": "open",
            "ride_id": ride_id,
            "reported_at": now.isoformat(),
            "created_at": now.isoformat(),
        }
        await _supabase_db.insert_one("safety_incidents", incident)
        # Mark escalated only after a successful insert so a DB failure does
        # not silently suppress future escalation attempts.
        await redis_set(_escalated_key(ride_id), "1", ttl=4 * 3600)
        logger.error(f"[SAFETY_CHECKIN] No response from rider {rider_id} on ride {ride_id}; safety incident opened.")

        # Notify the safety team — admin WS broadcast + email to the
        # configured distribution list + CRITICAL log line for on-call
        # paging. Replaces the inline broadcast_to_admins call that
        # only covered WS and left email/log paging unwired.
        try:
            await notify_safety_team(incident)
        except Exception as _notify_exc:
            logger.error(
                f"[SAFETY_CHECKIN] notify_safety_team failed for incident {incident['id']}: {_notify_exc}",
                exc_info=True,
            )

        # Write audit log entry for the automated escalation.
        try:
            await _log_audit(
                admin={"id": "system", "role": "system"},
                action="safety_incident_auto_escalated",
                resource="safety_incidents",
                resource_id=incident["id"],
                details={
                    "ride_id": ride_id,
                    "rider_id": rider_id,
                    "reason": "safety_checkin_no_response",
                    "escalate_after_seconds": _ESCALATE_AFTER_SECONDS,
                },
            )
        except Exception as _audit_exc:
            logger.error(f"[SAFETY_CHECKIN] Audit log write failed for incident {incident['id']}: {_audit_exc}")
    except Exception:
        logger.error(
            f"[SAFETY_CHECKIN] Failed to escalate ride {ride_id} — will retry next tick",
            exc_info=True,
        )
        # Re-raise so the caller (_tick) knows escalation failed and can log
        # context. The _escalated_key is NOT set, so the next tick will retry.
        raise
