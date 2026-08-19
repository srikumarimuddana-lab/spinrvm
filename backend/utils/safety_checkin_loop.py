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
running the same loop concurrently. ``safety:checkin:sent`` is claimed
atomically via ``SET NX`` *before* the push is sent (not read-then-write
after), so two replicas racing the same tick can't both fire the push —
see A40 finding #9/#14.

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
            _record_heartbeat("safety_checkin_loop")
        except Exception:
            logger.error("safety_checkin_loop tick failed", exc_info=True)
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

        sent_ts_str = await redis_get(_sent_key(ride_id))

        if not sent_ts_str:
            # A40 finding #9/#14 (2026-08-18 fleet audit): this used to be
            # check-then-act — read _sent_key, THEN send the push, THEN set
            # _sent_key — with a network round-trip (send_push_notification)
            # sitting in between the read and the write. Two replicas
            # polling the same ride in the same 30s tick could both read "not
            # sent" and both fire the FCM push before either wrote the claim.
            # Claim the key atomically FIRST via SET NX (same primitive this
            # codebase already uses elsewhere for leader-election/dedupe
            # locks, e.g. utils/referral_payout.py) so only one replica ever
            # proceeds to send.
            claimed = await redis_set_nx(_sent_key(ride_id), now.isoformat(), ttl=4 * 3600)
            if not claimed:
                continue  # another replica already claimed this check-in

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
                    # Release the claim so a genuinely failed send (not a
                    # duplicate) still gets retried on the next tick, same
                    # retry behavior this loop had before this fix.
                    try:
                        await redis_delete(_sent_key(ride_id))
                    except Exception:
                        logger.error(
                            f"[SAFETY_CHECKIN] could not release claim after failed push ride_id={ride_id}",
                            exc_info=True,
                        )
                    continue

            logger.info(f"[SAFETY_CHECKIN] Check-in sent for ride {ride_id}")
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
