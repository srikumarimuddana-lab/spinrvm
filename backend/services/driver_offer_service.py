"""v2 offer decisions: orchestrate resolve_driver_offer and its side effects.

The protocol follows the offer: only rows created by the v3 claim carry
``online_epoch``. Everything else stays on the legacy path, so no settings
read is needed to pick the path and a mid-offer flag flip stays correct.
A replayed decision (``replayed: true``) never repeats a side effect.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

try:
    from .. import db_supabase
    from ..repositories import driver_offer_repo
    from ..socket_manager import manager
    from ..utils.metrics import inc as _metric_inc
    from ..utils.redis_client import redis_set
except ImportError:  # pragma: no cover - top-level backend import mode
    import db_supabase  # type: ignore
    from repositories import driver_offer_repo  # type: ignore
    from socket_manager import manager  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set  # type: ignore

logger = logging.getLogger(__name__)

OFFER_COLUMNS = "id,status,claim_id,online_epoch,controller_session_id,offered_at,expires_at"


def is_v2_offer(row: dict | None) -> bool:
    """A v3-created offer: id, claim_id and online_epoch are all set."""
    return bool(row) and all(row.get(k) is not None for k in ("id", "claim_id", "online_epoch"))


def _won(result: dict) -> bool:
    return result.get("code") == "OK" and not result.get("replayed")


async def _update_acceptance_rate(driver_id: str, accepted: bool) -> None:
    try:
        from ..repositories.driver_repo import update_acceptance_rate
    except ImportError:  # pragma: no cover
        from repositories.driver_repo import update_acceptance_rate  # type: ignore
    await update_acceptance_rate(driver_id, accepted=accepted)


async def _set_offer_skip(ride_id: str, driver_id: str) -> None:
    try:
        await redis_set(f"spinr:offer_skip:{ride_id}:{driver_id}", "1", ttl=300)
    except Exception:
        logger.error("offer_skip key failed ride=%s driver=%s", ride_id, driver_id, exc_info=True)


async def _notify_driver(user_id: str | None, payload: dict) -> None:
    if not user_id:
        return
    try:
        await manager.send_personal_message(payload, f"driver_{user_id}")
    except Exception:
        logger.warning("offer WS %s failed user=%s", payload.get("type"), user_id, exc_info=True)


async def _notify_paused(result: dict) -> None:
    availability = result.get("availability") or {}
    await _notify_driver(
        result.get("driver_user_id"),
        {
            "type": "availability_changed",
            "online_epoch": availability.get("online_epoch"),
            "state_version": availability.get("state_version"),
            "reason_code": "MISSED_OFFERS",
            "server_time": availability.get("server_time") or result.get("server_time"),
        },
    )
    await _notify_driver(
        result.get("driver_user_id"),
        {
            "type": "auto_offline",
            "ride_id": result.get("ride_id"),
            "reason": "missed_offers",
            "miss_count": result.get("miss_streak"),
        },
    )


async def _load_offer(ride_id: str, driver_id: str) -> dict | None:
    rows = await db_supabase.get_rows(
        "ride_offers", {"ride_id": ride_id, "driver_id": driver_id}, columns=OFFER_COLUMNS, limit=1
    )
    return rows[0] if rows else None


def _decision_inputs(row: dict, body: dict | None, action: str, session_id: str) -> tuple[str, str, int, str]:
    body = body or {}
    offer_id = body.get("offer_id") or row["id"]
    claim_id = body.get("claim_id") or row["claim_id"]
    epoch = body.get("online_epoch")
    epoch = int(epoch) if epoch is not None else int(row["online_epoch"])
    request_id = body.get("request_id") or f"{action}:{offer_id}:{session_id}"[:128]
    return str(offer_id), str(claim_id), epoch, request_id


async def accept_offer_v2(ride_id: str, driver: dict, session_id: str | None, body: dict | None = None) -> dict | None:
    """Accept a v2 offer. None means legacy offer (caller keeps the legacy CAS)."""
    row = await _load_offer(ride_id, driver["id"])
    if not is_v2_offer(row) or not session_id:
        return None
    offer_id, claim_id, epoch, request_id = _decision_inputs(row, body, "accept", session_id)
    result = await driver_offer_repo.resolve_offer(
        offer_id, claim_id, action="accept", request_id=request_id, expected_epoch=epoch, actor_session_id=session_id
    )
    if result.get("code") == "AVAILABILITY_V2_DISABLED":
        return None
    if _won(result) and not result.get("already_accepted"):
        await _update_acceptance_rate(driver["id"], True)
        _metric_inc("spinr_dispatch_offer_accepted_total")
    return result


async def decline_offer_v2(
    ride_id: str, driver: dict, session_id: str | None, body: dict | None = None, reason: str | None = None
) -> dict | None:
    """Decline a v2 offer. None means legacy offer (caller keeps the legacy path)."""
    row = await _load_offer(ride_id, driver["id"])
    if not is_v2_offer(row) or not session_id:
        return None
    offer_id, claim_id, epoch, request_id = _decision_inputs(row, body, "decline", session_id)
    result = await driver_offer_repo.resolve_offer(
        offer_id, claim_id, action="decline", request_id=request_id, expected_epoch=epoch, actor_session_id=session_id
    )
    if result.get("code") == "AVAILABILITY_V2_DISABLED":
        return None
    if not _won(result):
        return result
    await _update_acceptance_rate(driver["id"], False)
    try:
        import uuid as _uuid

        await db_supabase.insert_one(
            "audit_logs",
            {
                "id": str(_uuid.uuid4()),
                "action": "ride_declined",
                "entity_type": "ride",
                "entity_id": ride_id,
                "actor_id": driver["id"],
                "details": {"driver_id": driver["id"], "reason": reason, "offer_id": offer_id},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.error("decline audit row failed ride=%s", ride_id, exc_info=True)
    await _set_offer_skip(ride_id, driver["id"])
    if result.get("remaining_pending_offers") == 0 and result.get("ride_status") == "searching":
        await _redispatch(ride_id)
    return result


async def _redispatch(ride_id: str) -> None:
    try:
        from ..routes.rides import match_driver_to_ride
        from ..utils.background import spawn
    except ImportError:  # pragma: no cover
        from routes.rides import match_driver_to_ride  # type: ignore
        from utils.background import spawn  # type: ignore
    spawn(match_driver_to_ride(ride_id))


async def release_preempted_losers(losers: list[dict], ride_id: str) -> None:
    """Release each preempted loser in its own transaction, then tell them."""
    for loser in losers or []:
        offer_id, claim_id = loser.get("offer_id"), loser.get("claim_id")
        if not offer_id or not claim_id:
            continue
        try:
            result = await driver_offer_repo.resolve_offer(
                offer_id, claim_id, action="cancel_unaccepted", request_id=f"preempted:{offer_id}"
            )
        except Exception:
            logger.error("preempted release failed ride=%s offer=%s", ride_id, offer_id, exc_info=True)
            continue
        if result.get("replayed"):
            continue
        await _notify_driver(result.get("driver_user_id"), {"type": "ride_taken", "ride_id": ride_id})


async def expire_offer_v2(offer_row: dict, threshold: int) -> bool:
    """Expire one v2 offer. Returns True only when this call made the decision."""
    offer_id = str(offer_row["id"])
    result = await driver_offer_repo.resolve_offer(
        offer_id,
        str(offer_row["claim_id"]),
        action="expire",
        request_id=f"expire:{offer_id}",
        miss_threshold=max(1, min(int(threshold), 20)),
    )
    if not _won(result):
        return False
    outcome = result.get("outcome")
    _metric_inc("spinr_dispatch_offer_terminal_total", {"outcome": str(outcome)})
    driver_id, ride_id = result.get("driver_id"), result.get("ride_id")
    if result.get("miss_counted"):
        await _update_acceptance_rate(driver_id, False)
    await _set_offer_skip(ride_id, driver_id)
    if result.get("paused"):
        await _notify_paused(result)
    else:
        await _notify_driver(
            result.get("driver_user_id"),
            {
                "type": "ride_offer_expired",
                "ride_id": ride_id,
                "offer_id": offer_id,
                "claim_id": result.get("claim_id"),
                "outcome": outcome,
            },
        )
    return True


async def release_cancelled_offer_v2(row: dict) -> dict:
    """Release the claim of an offer whose ride was cancelled."""
    offer_id = str(row["id"])
    return await driver_offer_repo.resolve_offer(
        offer_id, str(row["claim_id"]), action="cancel_unaccepted", request_id=f"cancel:{offer_id}"
    )
