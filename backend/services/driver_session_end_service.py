"""Stop driver requests when an auth session ends (T11-B1, design A1/X5, C8/C9).

``stop_requests_for_session_end`` runs T1 ``stop_requests`` as the trusted
``system:logout`` actor. T1 then decides the outcome under its own locks:

* no obligation -> the driver goes offline and Period 0 opens;
* an obligation -> the driver stays online but stops accepting; the trip and
  Period 2/3 are kept;
* a pending offer expires as ``expired_availability_changed`` (the epoch has
  moved, so no miss is counted).

Return values:

* ``"legacy"``  -- v2 is off, unreadable, or the driver has no controller;
  the caller keeps its flag-off cleanup.
* ``"skipped"`` -- nothing to stop (no driver, offline, or ``logout`` from a
  session that is not the controller).
* ``"stopped"`` -- T1 committed the stop.
* ``"failed"``  -- the stop did not commit. Callers must NOT fall back to raw
  offline or offer writes under v2: those break epoch rules. The v3 claim
  predicate and the contact-gap reconciler cover a failed stop.

Never raises.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

try:
    from ..repositories import driver_availability_repo
    from ..settings_loader import get_app_settings
    from ..socket_manager import manager
    from ..utils.driver_presence import clear_scoped_driver_presence
except ImportError:  # pragma: no cover - top-level backend import mode
    from repositories import driver_availability_repo  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from socket_manager import manager  # type: ignore
    from utils.driver_presence import clear_scoped_driver_presence  # type: ignore

logger = logging.getLogger(__name__)

SessionEndCause = Literal["logout", "logout-all", "superseded"]
SessionEndResult = Literal["stopped", "skipped", "legacy", "failed"]

_CAUSES = frozenset({"logout", "logout-all", "superseded"})
_REQUEST_ID_MAX = 128


def _request_id(cause: str, ended_session_id: Optional[str], epoch: int) -> str:
    return f"{cause}:{ended_session_id or 'all'}:{epoch}"[:_REQUEST_ID_MAX]


def _classify(
    raw: Any, cause: str, ended_session_id: Optional[str]
) -> tuple[Optional[SessionEndResult], Optional[dict[str, Any]]]:
    """Step 2 of X5. Returns (terminal result, None) or (None, driver)."""
    if not isinstance(raw, dict) or not raw.get("protocol_enabled"):
        return "legacy", None
    driver = raw.get("driver")
    if not isinstance(driver, dict) or not driver.get("id"):
        return "skipped", None
    controller = driver.get("controller_session_id")
    if not controller:
        return "legacy", None
    if not driver.get("is_online"):
        return "skipped", None
    if cause == "logout" and str(controller) != str(ended_session_id or ""):
        return "skipped", None
    epoch = driver.get("online_epoch")
    if type(epoch) is not int or epoch < 0:
        raise ValueError("driver online_epoch is not a non-negative integer")
    return None, driver


async def _notify_availability_changed(user_id: str, result: dict[str, Any]) -> None:
    reason = "OFFLINE_INTENT" if result.get("is_online") is False else "REQUESTS_STOPPED"
    try:
        await manager.send_personal_message(
            {
                "type": "availability_changed",
                "online_epoch": result.get("online_epoch"),
                "state_version": result.get("state_version"),
                "reason_code": reason,
                "server_time": result.get("server_time"),
            },
            f"driver_{user_id}",
        )
    except Exception:
        logger.warning("session_end: availability_changed WS failed user=%s", user_id, exc_info=True)


async def stop_requests_for_session_end(
    user_id: str,
    *,
    cause: SessionEndCause,
    ended_session_id: Optional[str],
) -> SessionEndResult:
    """Stop offers for ``user_id`` after a session ends; see module docstring."""
    if cause not in _CAUSES:
        raise ValueError(f"unsupported session end cause: {cause}")

    # Step 1: flag. A read error or a missing flag is legacy.
    try:
        settings = await get_app_settings()
    except Exception:
        logger.warning("session_end: settings read failed; legacy path user=%s", user_id, exc_info=True)
        return "legacy"
    if not (settings or {}).get("driver_availability_v2_enabled"):
        return "legacy"

    try:
        for attempt in range(2):
            # Step 2: authoritative snapshot (re-read on the single stale retry).
            raw = await driver_availability_repo.get_driver_availability_snapshot(user_id)
            terminal, driver = _classify(raw, cause, ended_session_id)
            if terminal is not None or driver is None:
                return terminal or "failed"
            driver_id = str(driver["id"])
            epoch = int(driver["online_epoch"])
            controller = str(driver["controller_session_id"])

            # Step 3: T1 stop through the trusted logout actor.
            result = await driver_availability_repo.transition_driver_availability(
                driver_id,
                epoch,
                driver_availability_repo.system_actor("logout"),
                "stop_requests",
                _request_id(cause, ended_session_id, epoch),
            )
            code = result.get("code")
            if code == "ONLINE_EPOCH_STALE" and attempt == 0:
                continue
            if code != "OK":
                logger.error(
                    "session_end: stop_requests refused user=%s driver=%s cause=%s code=%s",
                    user_id,
                    driver_id,
                    cause,
                    code,
                )
                return "failed"
            try:
                await clear_scoped_driver_presence(driver_id, controller, epoch)
            except Exception:
                logger.warning("session_end: scoped presence clear failed driver=%s", driver_id, exc_info=True)
            if not result.get("replayed"):
                await _notify_availability_changed(user_id, result)
            return "stopped"
    except Exception:
        logger.error("session_end: stop_requests failed user=%s cause=%s", user_id, cause, exc_info=True)
        return "failed"
    return "failed"
