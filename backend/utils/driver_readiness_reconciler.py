"""Driver readiness reconciler (T12-5): contact-gap and readiness pauses, prompts.

Every 20 s one replica (best-effort Redis leader lock) asks the database for
drivers that are due and acts on each through fenced RPCs:

  * ``contact_gap``   -> ``reconcile_driver_readiness(..., 'contact_gap')``
    (T1 ``pause_unreachable`` as ``system:contact_gap``);
  * ``readiness_due`` -> ``reconcile_driver_readiness(..., 'readiness')``
    (T1 ``pause_idle`` as ``system:readiness``; listed only when enforced);
  * ``prompt_due``    -> ``claim_readiness_prompt`` (at most once per
    ``ready_until``), then WS ``availability_readiness_prompt`` + push.

Replay-safety: every write is fenced by the driver's online epoch, taken
``FOR UPDATE SKIP LOCKED`` and re-checked on the DB clock, and the T1 request
ids are ``contact-gap:{driver}:{epoch}`` / ``readiness:{driver}:{epoch}``.
Duplicate workers are therefore harmless, which is why the leader lock fails
open. A settings or list failure skips the tick: never pause on guesswork.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

try:
    from ..features import send_push_notification
    from ..repositories import driver_availability_repo
    from ..settings_loader import get_app_settings
    from ..socket_manager import manager
    from .metrics import inc as _metric_inc
    from .redis_client import try_acquire_leader_lock
except ImportError:  # pragma: no cover - top-level backend import mode
    from features import send_push_notification  # type: ignore
    from repositories import driver_availability_repo  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from socket_manager import manager  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import try_acquire_leader_lock  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:  # pragma: no cover
    try:
        from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    except ImportError:

        def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
            pass


logger = logging.getLogger(__name__)

LOOP_NAME = "driver_readiness_reconciler (20s)"
LOCK_NAME = "driver_readiness_reconciler"  # key spinr:driver_readiness_reconciler:lock
INTERVAL_SECONDS = 20
CANDIDATE_LIMIT = 100

_REASON_BY_KIND = {"contact_gap": "PRESENCE_UNAVAILABLE", "readiness": "READY_TIMEOUT"}
_REQUEST_PREFIX = {"contact_gap": "contact-gap", "readiness": "readiness"}
_PAUSE_PUSH = {
    "contact_gap": (
        "You're paused",
        "We lost contact with your app, so ride requests are paused. Open Spinr to continue.",
    ),
    "readiness": (
        "Ride requests paused",
        "You didn't confirm you're still available, so ride requests are paused.",
    ),
}


def _epoch(row: dict[str, Any]) -> int | None:
    raw = row.get("online_epoch")
    text = str(raw) if raw is not None else ""
    return int(text) if text.isascii() and text.isdecimal() else None


async def _send_ws(user_id: str | None, payload: dict[str, Any]) -> None:
    if not user_id:
        return
    try:
        await manager.send_personal_message(payload, f"driver_{user_id}")
    except Exception:
        logger.warning("readiness reconciler WS %s failed user=%s", payload.get("type"), user_id, exc_info=True)


async def _send_push(user_id: str | None, title: str, body: str, data: dict[str, str], priority: str) -> None:
    if not user_id:
        return
    try:
        await send_push_notification(user_id, title, body, data=data, priority=priority, target_app="driver")
    except Exception:
        logger.warning("readiness reconciler push failed user=%s", user_id, exc_info=True)


async def _pause(kind: str, row: dict[str, Any]) -> str:
    driver_id = row.get("driver_id")
    epoch = _epoch(row)
    if not driver_id or epoch is None:
        return "invalid"
    request_id = f"{_REQUEST_PREFIX[kind]}:{driver_id}:{epoch}"
    try:
        result = await driver_availability_repo.reconcile_driver_readiness(str(driver_id), epoch, kind, request_id)
    except Exception:
        logger.error("readiness reconcile failed driver=%s kind=%s", driver_id, kind, exc_info=True)
        return "error"
    code = result.get("code") or "UNKNOWN"
    _metric_inc("spinr_driver_readiness_reconcile_total", {"kind": kind, "code": code})
    if code != "OK" or result.get("replayed"):
        return code.lower()
    user_id = result.get("user_id") or row.get("user_id")
    await _send_ws(
        user_id,
        {
            "type": "availability_changed",
            "online_epoch": result.get("online_epoch"),
            "state_version": result.get("state_version"),
            "reason_code": _REASON_BY_KIND[kind],
            "server_time": result.get("server_time"),
        },
    )
    title, body = _PAUSE_PUSH[kind]
    await _send_push(user_id, title, body, {"type": "availability_changed", "reason": kind}, "normal")
    return "paused"


async def _prompt(row: dict[str, Any], server_time: Any) -> str:
    driver_id = row.get("driver_id")
    epoch = _epoch(row)
    ready_until = row.get("ready_until")
    if not driver_id or epoch is None or not ready_until:
        return "invalid"
    try:
        user_id = await driver_availability_repo.claim_readiness_prompt(str(driver_id), epoch, str(ready_until))
    except Exception:
        logger.error("readiness prompt claim failed driver=%s", driver_id, exc_info=True)
        return "error"
    if not user_id:
        return "not_claimed"
    _metric_inc("spinr_driver_readiness_prompt_total", {"outcome": "claimed"})
    await _send_ws(
        user_id,
        {
            "type": "availability_readiness_prompt",
            "online_epoch": str(epoch),
            "ready_until": ready_until,
            "server_time": server_time,
        },
    )
    await _send_push(
        user_id,
        "Still available for ride requests?",
        "Tap to keep receiving ride requests.",
        {"type": "availability_readiness_prompt", "online_epoch": str(epoch), "ready_until": str(ready_until)},
        "dispatch",
    )
    return "prompted"


async def reconcile_tick() -> dict[str, int]:
    """One pass. Returns counters for logging and tests."""
    stats = {"paused": 0, "prompted": 0, "skipped": 0}
    try:
        settings = await get_app_settings()
    except Exception:
        logger.error("readiness reconciler: settings read failed; tick skipped", exc_info=True)
        stats["skipped"] = 1
        return stats
    if not (settings or {}).get("driver_availability_v2_enabled"):
        return stats
    try:
        listed = await driver_availability_repo.list_availability_reconcile_candidates(CANDIDATE_LIMIT)
    except Exception:
        logger.error("readiness reconciler: candidate list failed; tick skipped", exc_info=True)
        stats["skipped"] = 1
        return stats

    for kind, key in (("contact_gap", "contact_gap"), ("readiness", "readiness_due")):
        for row in listed.get(key) or []:
            if isinstance(row, dict) and await _pause(kind, row) == "paused":
                stats["paused"] += 1
    for row in listed.get("prompt_due") or []:
        if isinstance(row, dict) and await _prompt(row, listed.get("server_time")) == "prompted":
            stats["prompted"] += 1
    if stats["paused"] or stats["prompted"]:
        logger.info("readiness reconciler: %s", stats)
    return stats


async def driver_readiness_reconciler_loop() -> None:
    """Every 20 s: pause unreachable or idle-expired drivers and send prompts."""
    logger.info("Driver readiness reconciler started")
    while True:
        try:
            if await try_acquire_leader_lock(LOCK_NAME, int(INTERVAL_SECONDS * 0.85)):
                await reconcile_tick()
        except Exception:
            logger.error("driver_readiness_reconciler tick failed", exc_info=True)
        _record_heartbeat(LOOP_NAME)
        await asyncio.sleep(INTERVAL_SECONDS * random.uniform(0.9, 1.1))
