"""WebSocket fan-out over Redis pub/sub (audit P0-B3).

The in-process ``ConnectionManager`` in ``backend/socket_manager.py``
stores sockets in ``active_connections[client_id]``. On a single VM
that's correct; on a multi-machine Fly deploy it silently breaks —
``manager.send_personal_message({...}, "rider_abc")`` only reaches the
rider if the rider happens to be connected to the same machine that's
calling it. With Fly's default sticky-ish LB, roughly half of ride
events go to the wrong VM and never reach the client.

Design (Socket.IO Redis-adapter style):

  1. Every machine subscribes to ONE shared channel, ``CHANNEL``.
  2. Every outbound ``send_personal_message`` is published to the
     channel as ``{"client_id": <key>, "message": <payload>}``.
  3. Every machine's subscriber receives EVERY message (including its
     own, via Redis loopback) and delivers locally iff the client is
     in its ``active_connections`` dict. Non-local messages are dropped.
  4. Redis outage ⇒ ``publish()`` returns False and the caller falls
     back to the original local-only path. That degrades to today's
     behaviour (works for the VM that has the socket; silent miss for
     the others) rather than bringing WS traffic down entirely.

This module owns the Redis client and the consumer task; it is
started from ``core/lifespan.py`` and stopped during shutdown so the
background task is cancelled cleanly.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from loguru import logger

# Single shared channel is fine at our scale: at N hundred active
# sockets the extra Redis bandwidth from receiving messages not
# destined for this machine is negligible compared to the cost of
# managing one channel per client (subscribe/unsubscribe on every
# connect/disconnect against Redis). If this ever becomes hot we can
# switch to pattern-subscribe per-{role} without a migration.
CHANNEL = "spinr:ws:dispatch"


class _WSPubSub:
    """Stateful Redis pub/sub driver for the WebSocket manager.

    Singleton; access via the module-level ``pubsub`` instance. Methods
    are safe to call even when Redis isn't configured — they'll no-op
    and return False so callers can fall back to local delivery.
    """

    def __init__(self) -> None:
        self._redis: Any = None
        self._pubsub: Any = None
        self._task: Optional[asyncio.Task] = None
        self._manager: Any = None
        self._url: str = ""

    @property
    def active(self) -> bool:
        """True iff we have a live Redis connection and a running consumer."""
        return self._redis is not None and self._task is not None and not self._task.done()

    async def start(self, manager: Any, redis_url: str) -> bool:
        """Connect to Redis, subscribe to the channel, spawn the consumer.

        ``manager`` is the ConnectionManager instance — we keep a
        reference so the consumer can deliver incoming messages to its
        local sockets. Returns True on success, False if Redis is
        unreachable (the caller keeps running; local-only delivery).
        """
        if not redis_url:
            logger.info("WS pub/sub: no Redis URL configured — single-machine mode")
            return False

        try:
            import redis.asyncio as redis_asyncio  # type: ignore
        except ImportError:
            logger.warning("WS pub/sub: redis package not installed; single-machine mode")
            return False

        try:
            client = redis_asyncio.from_url(redis_url, decode_responses=True)
            await client.ping()
        except Exception as e:
            # Never log the URL; it contains the password.
            logger.error(f"WS pub/sub: could not connect to Redis ({type(e).__name__}) — falling back to local-only")
            return False

        self._redis = client
        self._manager = manager
        self._url = redis_url

        try:
            self._pubsub = client.pubsub()
            await self._pubsub.subscribe(CHANNEL)
        except Exception as e:
            logger.error(f"WS pub/sub: subscribe failed: {e}")
            await self._safe_close_pubsub()
            await self._safe_close_redis()
            return False

        self._task = asyncio.create_task(self._consumer(), name="ws_pubsub_consumer")
        scheme = redis_url.split("://", 1)[0]
        logger.info(f"WS pub/sub started (backend={scheme}://…, channel={CHANNEL})")
        return True

    async def publish(self, client_id: str, message: dict) -> bool:
        """Publish a message to the shared channel.

        Returns True if the message was handed to Redis; False means
        Redis is not active and the caller must deliver locally.
        Publishing is fire-and-forget from the caller's perspective —
        the acknowledgement tells us Redis accepted it, not that any
        subscriber has yet received it. That's fine: the sender's own
        machine is a subscriber, so a successful publish guarantees at
        least best-effort local delivery within milliseconds.
        """
        if not self.active:
            return False
        try:
            body = json.dumps({"client_id": client_id, "message": message})
        except (TypeError, ValueError) as e:
            # Non-JSON-serialisable payloads would cause every message
            # to fail silently if we swallowed this; log loudly.
            logger.error(f"WS pub/sub: could not serialise message for {client_id}: {e}")
            return False
        try:
            await self._redis.publish(CHANNEL, body)
            return True
        except Exception as e:
            logger.warning(f"WS pub/sub: publish failed, falling back to local delivery: {e}")
            return False

    async def _consumer(self) -> None:
        """Long-running task that delivers incoming messages locally.

        Uses the blocking ``get_message`` loop rather than ``listen()``
        so we can react to cancellation on a fixed cadence. 1s timeout
        is plenty — the consumer is event-driven and the sleep is only
        hit when idle.
        """
        try:
            while True:
                try:
                    msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"WS pub/sub: consumer read error: {e}")
                    # Back off briefly so a wedged Redis doesn't spin.
                    await asyncio.sleep(1.0)
                    continue

                if not msg:
                    continue
                if msg.get("type") != "message":
                    continue

                try:
                    data = json.loads(msg.get("data") or "{}")
                except (TypeError, ValueError):
                    continue

                client_id = data.get("client_id")
                payload = data.get("message")
                if not client_id or payload is None:
                    continue

                try:
                    await self._manager._deliver_local(payload, client_id)
                except Exception as e:
                    # One bad delivery must not kill the consumer.
                    logger.warning(f"WS pub/sub: local delivery failed for {client_id}: {e}")
        except asyncio.CancelledError:
            logger.info("WS pub/sub consumer cancelled")
            raise

    async def stop(self) -> None:
        """Cancel the consumer and close the Redis connection."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: S110
                pass
            self._task = None

        await self._safe_close_pubsub()
        await self._safe_close_redis()
        self._manager = None
        logger.info("WS pub/sub stopped")

    async def _safe_close_pubsub(self) -> None:
        if self._pubsub is None:
            return
        try:
            await self._pubsub.unsubscribe(CHANNEL)
        except Exception:  # noqa: S110
            pass
        try:
            await self._pubsub.close()
        except Exception:  # noqa: S110
            pass
        self._pubsub = None

    async def _safe_close_redis(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.close()
        except Exception:  # noqa: S110
            pass
        self._redis = None


pubsub = _WSPubSub()


def resolve_ws_redis_url(ws_url: str, rate_limit_url: str) -> str:
    """Pick the effective Redis URL for WS pub/sub.

    Prefer ``WS_REDIS_URL`` when set; otherwise reuse
    ``RATE_LIMIT_REDIS_URL`` so operators only need to configure one
    value. Returns empty string if neither is set — the caller then
    runs in single-machine mode.
    """
    return (ws_url or "").strip() or (rate_limit_url or "").strip()
