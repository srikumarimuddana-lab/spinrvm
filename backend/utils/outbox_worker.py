"""Dedicated outbox poller: claim, deliver, ack/fail/discard, metrics."""

from __future__ import annotations

import asyncio
import os
import socket
import time
from typing import Any, Dict, Optional

from loguru import logger as _raw_logger

try:
    from ..core.config import settings
    from ..services import outbox as outbox_repo
    from ..services import payment_service as payment_service
    from ..utils.email_provider import EmailDeliveryStatus
    from ..utils.loop_monitor import record_heartbeat
    from ..utils.metrics import inc, set_gauge
except ImportError:
    from core.config import settings  # type: ignore
    from services import outbox as outbox_repo  # type: ignore
    from services import payment_service as payment_service  # type: ignore
    from utils.email_provider import EmailDeliveryStatus  # type: ignore
    from utils.loop_monitor import record_heartbeat  # type: ignore
    from utils.metrics import inc, set_gauge  # type: ignore

logger = _raw_logger.bind(domain="payments", surface="backend")

BUSY_POLL_S = 1.0
IDLE_POLL_S = 10.0
_LOOP_NAME = "outbox_poller (1-10s)"
_CLEANUP_EVERY_S = 60.0

_last_cleanup_mono = 0.0


def _ride_id_from_payload(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        ride_id = payload.get("ride_id")
        if isinstance(ride_id, str) and ride_id:
            return ride_id
    return None


def _payload_is_ride_receipt(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if set(payload.keys()) != {"ride_id"}:
        return False
    ride_id = payload.get("ride_id")
    return isinstance(ride_id, str) and bool(ride_id)


def _capture_dead_letter(topic: str, payload: Any, *, msg_id: str = "") -> None:
    ride_id = _ride_id_from_payload(payload)
    logger.info(
        "outbox dead-letter topic={} id={} ride_id={}",
        topic,
        msg_id or "-",
        ride_id or "-",
    )
    try:
        import sentry_sdk

        scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with scope_cm() as scope:
            scope.set_tag("domain", "payments")
            scope.set_tag("surface", "backend")
            scope.set_tag("env", getattr(settings, "ENV", "development"))
            if ride_id:
                scope.set_tag("ride_id", ride_id)
            sentry_sdk.capture_message(
                f"outbox message dead-lettered topic={topic} ride_id={ride_id or '-'}",
                level="error",
            )
    except Exception:
        logger.error("outbox dead-letter Sentry capture failed topic={}", topic, exc_info=True)
    inc("spinr_outbox_dead_lettered_total", {"topic": topic})


async def _fail_and_maybe_dead_letter(
    msg_id: str,
    token: str,
    topic: str,
    payload: Any,
    attempt: int,
    max_attempts: int,
    code: str,
) -> None:
    ok = await outbox_repo.fail(msg_id, token, code)
    inc("spinr_outbox_retry_total", {"topic": topic})
    if ok and attempt >= max_attempts:
        _capture_dead_letter(topic, payload, msg_id=msg_id)
    elif not ok:
        logger.warning("outbox fail stale-token no-op id={}", msg_id)


async def _refresh_gauges() -> None:
    try:
        rows = await outbox_repo.stats()
    except Exception:
        logger.error("outbox_stats failed", exc_info=True)
        return
    pending = 0
    oldest = None
    now = time.time()
    for row in rows or []:
        status = row.get("status")
        count = int(row.get("message_count") or row.get("count") or 0)
        if status == "pending":
            pending += count
            ts = row.get("oldest_available_at")
            if ts is not None:
                oldest = ts
    set_gauge("spinr_outbox_pending_messages", float(pending))
    age = 0.0
    if oldest is not None:
        try:
            if hasattr(oldest, "timestamp"):
                age = max(0.0, now - oldest.timestamp())
            elif isinstance(oldest, str):
                from datetime import datetime, timezone

                parsed = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age = max(0.0, now - parsed.timestamp())
        except Exception:
            age = 0.0
    set_gauge("spinr_outbox_oldest_pending_age_seconds", age)


async def _maybe_cleanup() -> None:
    global _last_cleanup_mono
    now = time.monotonic()
    if now - _last_cleanup_mono < _CLEANUP_EVERY_S:
        return
    _last_cleanup_mono = now
    try:
        await outbox_repo.cleanup()
    except Exception:
        logger.error("outbox_cleanup failed", exc_info=True)


async def _dispatch(msg: Dict[str, Any]) -> None:
    topic = msg.get("topic") or ""
    token = msg.get("lease_token") or ""
    msg_id = msg.get("id") or ""
    payload = msg.get("payload")
    attempt = int(msg.get("attempt_count") or 0)
    max_attempts = int(msg.get("max_attempts") or 8)

    if topic != outbox_repo.TOPIC_RIDE_RECEIPT:
        logger.error("outbox unknown topic={} id={} — retry then dead-letter", topic, msg_id)
        await _fail_and_maybe_dead_letter(msg_id, token, topic, payload, attempt, max_attempts, "unknown_topic")
        return

    if not _payload_is_ride_receipt(payload):
        logger.error("outbox malformed ride_receipt payload id={}", msg_id)
        ok = await outbox_repo.discard(msg_id, token, "malformed_payload")
        if ok:
            inc("spinr_outbox_completed_total", {"topic": topic, "outcome": "discarded"})
        return

    try:
        result = await payment_service.send_ride_receipt_result(payload["ride_id"])
    except Exception:
        logger.error("outbox ride_receipt handler raised id={}", msg_id, exc_info=True)
        await _fail_and_maybe_dead_letter(msg_id, token, topic, payload, attempt, max_attempts, "provider_unavailable")
        return

    if result.status == EmailDeliveryStatus.accepted:
        ok = await outbox_repo.ack(msg_id, token)
        if ok:
            inc("spinr_outbox_completed_total", {"topic": topic, "outcome": "published"})
        else:
            logger.warning("outbox ack stale-token no-op id={}", msg_id)
        return

    if result.status == EmailDeliveryStatus.terminal_skip:
        code = result.error_code or "no_recipient"
        ok = await outbox_repo.discard(msg_id, token, code)
        if ok:
            inc("spinr_outbox_completed_total", {"topic": topic, "outcome": "discarded"})
        else:
            logger.warning("outbox discard stale-token no-op id={}", msg_id)
        return

    code = result.error_code or "provider_unavailable"
    await _fail_and_maybe_dead_letter(msg_id, token, topic, payload, attempt, max_attempts, code)


async def outbox_tick(worker_id: str) -> int:
    """Claim and process one batch. Returns the number of claimed processing rows."""
    try:
        rows = await outbox_repo.claim_batch(worker_id)
    except Exception:
        logger.error("outbox_claim_batch failed", exc_info=True)
        return 0
    expired = [msg for msg in rows if msg.get("status") == "dead_lettered"]
    claimed = [msg for msg in rows if msg.get("status") != "dead_lettered"]
    for msg in expired:
        _capture_dead_letter(
            msg.get("topic") or "",
            msg.get("payload"),
            msg_id=str(msg.get("id") or ""),
        )
    for msg in claimed:
        try:
            await _dispatch(msg)
        except Exception:
            logger.error("outbox dispatch failed id={}", msg.get("id"), exc_info=True)
    await _refresh_gauges()
    if not claimed:
        await _maybe_cleanup()
    record_heartbeat(_LOOP_NAME)
    return len(claimed)


async def run_outbox_worker(stop_event: asyncio.Event, worker_id: Optional[str] = None) -> None:
    """Poll until stop_event is set. 1s after work, 10s while idle."""
    worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    logger.info("outbox worker started worker_id={}", worker_id)
    while not stop_event.is_set():
        try:
            n = await outbox_tick(worker_id)
        except Exception:
            logger.error("outbox_tick failed", exc_info=True)
            n = 0
        timeout = BUSY_POLL_S if n else IDLE_POLL_S
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            continue
