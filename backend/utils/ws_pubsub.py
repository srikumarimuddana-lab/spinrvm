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
     channel as
     ``{"kind": "unicast", "client_id": <key>, "message": <payload>}``.
     Broadcasts (e.g. admin live monitoring) publish
     ``{"kind": "broadcast", "prefix": <key-prefix>, "message": <payload>}``
     and every VM delivers to its local connections whose key starts
     with ``prefix``.
  3. Every machine's subscriber receives EVERY message (including its
     own, via Redis loopback) and delivers locally iff the client is
     in its ``active_connections`` dict. Non-local messages are dropped.
  4. Redis outage ⇒ ``publish()`` / ``publish_broadcast()`` return False
     and the caller falls back to the original local-only path. That
     degrades to today's behaviour (works for the VM that has the
     socket; silent miss for the others) rather than bringing WS
     traffic down entirely.

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
        # Last connect/subscribe failure reason, surfaced to the admin
        # WS-health card so "fan-out is local-only" comes with a why.
        # Never holds the URL (which carries the password).
        self._last_error: Optional[str] = None

    @property
    def active(self) -> bool:
        """True iff we have a live Redis connection, a live subscription, and
        a running consumer.

        ``_pubsub`` is included deliberately: after a runtime Redis drop a
        failed ``_reconnect()`` nulls ``_pubsub`` but leaves the consumer task
        looping in backoff, so checking only ``_redis``/``_task`` would report
        "Live" while this replica is no longer subscribed to the fan-out
        channel. Tying ``active`` to the subscription keeps the health endpoint
        honest during a reconnect-failure window.
        """
        return self._redis is not None and self._pubsub is not None and self._task is not None and not self._task.done()

    def status(self) -> dict:
        """Snapshot of cross-replica fan-out health on THIS replica.

        ``active`` True means this process holds a live Redis subscription and
        a running consumer, so broadcasts published by *other* replicas reach
        the admin sockets connected here. False means local-only delivery —
        the admin live map only sees clients on this same worker, which is the
        failure mode when Redis is unreachable. ``configured`` distinguishes
        "no Redis URL set (single-machine by design)" from "URL set but the
        connection failed" (see ``last_error``).
        """
        scheme = self._url.split("://", 1)[0] if self._url else ""
        return {
            "active": self.active,
            "channel": CHANNEL,
            "backend_scheme": scheme,
            "configured": bool(self._url),
            "last_error": self._last_error,
        }

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

        # Record that fan-out is *configured* before we attempt the connection,
        # so status() reports configured=True (and the right scheme) even when
        # the connect/subscribe below fails. Otherwise a Redis-unreachable
        # backend would mislabel itself "single-machine" and suppress the
        # degraded banner — the exact state we built this to surface.
        self._url = redis_url

        try:
            client = redis_asyncio.from_url(redis_url, decode_responses=True)
            await client.ping()
        except Exception as e:
            # Never log the URL; it contains the password.
            self._last_error = f"connect: {type(e).__name__}"
            logger.error(f"WS pub/sub: could not connect to Redis ({type(e).__name__}) — falling back to local-only")
            return False

        self._redis = client
        self._manager = manager

        try:
            self._pubsub = client.pubsub()
            await self._pubsub.subscribe(CHANNEL)
        except Exception as e:
            self._last_error = f"subscribe: {type(e).__name__}"
            logger.error(f"WS pub/sub: subscribe failed: {e}")
            await self._safe_close_pubsub()
            await self._safe_close_redis()
            return False

        self._last_error = None
        self._task = asyncio.create_task(self._consumer(), name="ws_pubsub_consumer")
        scheme = redis_url.split("://", 1)[0]
        logger.info(f"WS pub/sub started (backend={scheme}://…, channel={CHANNEL})")
        return True

    async def publish(self, client_id: str, message: dict, *, durable: bool = True) -> bool:
        """Publish a unicast message to the shared channel.

        ``durable=False`` skips the seq/outbox bookkeeping: ephemeral
        messages (1 Hz driver-location fan-out) were filling the 50-entry
        per-client replay ring in under a minute, evicting the durable ride
        events (offers, ride_taken, status changes) that ``?last_seq``
        reconnect recovery exists for. Non-durable messages are sent
        unwrapped — the same shape clients already handle on the
        Redis-outbox-failure path — and the next ping supersedes them
        anyway, so replay would be pointless.

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

        # Durable path is 2 Redis round-trips (was 3): one INCR for the seq
        # (its result builds seq_message, so it can't fold into a
        # non-transactional pipeline), then ONE pipeline that both buffers the
        # outbox AND publishes.
        seq_message: Any = message
        buffered = False
        if durable:
            try:
                seq = await self._redis.incr(f"spinr:ws:seq:{client_id}")
                seq_message = {"seq": seq, "data": message}
                buffered = True
            except Exception as e:
                logger.error(f"WS pub/sub: seq incr failed for {client_id}: {e}")
                seq_message = message

        try:
            body = json.dumps({"kind": "unicast", "client_id": client_id, "message": seq_message})
        except (TypeError, ValueError) as e:
            # Non-JSON-serialisable payloads would cause every message
            # to fail silently if we swallowed this; log loudly.
            logger.error(f"WS pub/sub: could not serialise message for {client_id}: {e}")
            return False

        if buffered:
            # Outbox writes + PUBLISH in a single round-trip.
            try:
                outbox_key = f"spinr:ws:outbox:{client_id}"
                pipe = self._redis.pipeline(transaction=False)
                pipe.rpush(outbox_key, json.dumps(seq_message))
                pipe.ltrim(outbox_key, -50, -1)
                pipe.expire(outbox_key, 300)
                pipe.publish(CHANNEL, body)
                await pipe.execute()
                return True
            except Exception as e:
                # Durability bookkeeping failed — still deliver via a bare
                # publish below rather than dropping the event.
                logger.error(f"WS pub/sub: durable pipeline failed for {client_id}, bare-publishing: {e}")

        try:
            await self._redis.publish(CHANNEL, body)
            return True
        except Exception as e:
            logger.error(f"WS pub/sub: publish failed, falling back to local delivery: {e}", exc_info=True)
            return False

    async def get_outbox(self, client_id: str) -> list:
        """Return all buffered outbox messages for a client (up to 50)."""
        if not self.active:
            return []
        try:
            raw = await self._redis.lrange(f"spinr:ws:outbox:{client_id}", 0, -1)
            return [json.loads(m) for m in raw]
        except Exception as e:
            logger.warning(f"WS pub/sub: get_outbox({client_id}) failed: {e}")
            return []

    async def publish_broadcast(self, prefix: str, message: dict) -> bool:
        """Publish a prefix-broadcast to every VM.

        Each replica delivers ``message`` to every local connection whose
        key starts with ``prefix`` (e.g. ``admin_`` for admin live
        monitoring). Without this, ``broadcast_to_admins`` only reaches
        admins on the VM that happened to trigger the event — an admin
        on another replica would miss driver status changes.
        """
        if not self.active:
            return False
        try:
            body = json.dumps({"kind": "broadcast", "prefix": prefix, "message": message})
        except (TypeError, ValueError) as e:
            logger.error(f"WS pub/sub: could not serialise broadcast for {prefix}: {e}")
            return False
        try:
            await self._redis.publish(CHANNEL, body)
            return True
        except Exception as e:
            logger.error(f"WS pub/sub: broadcast publish failed, falling back to local: {e}", exc_info=True)
            return False

    async def publish_kick_user(
        self,
        user_id: str,
        *,
        client_types: list | None = None,
        reason: str = "token_revoked",
    ) -> bool:
        """Broadcast a "force-disconnect this user" control message (B-P1-11).

        Each replica's consumer sees the message and calls
        ``manager.disconnect_user`` against its own local socket dict;
        replicas with no matching sockets no-op silently. The control
        envelope is distinct from the unicast/broadcast envelopes used
        by ``publish``/``publish_broadcast`` so the consumer can route
        on shape and existing in-flight messages remain unaffected.

        Returns True iff the message was handed to Redis. False means
        single-machine mode (caller still owns the local kick) or a
        Redis hiccup — either way the local-only path stays correct.
        """
        if not self.active:
            return False
        try:
            body = json.dumps(
                {
                    "control": {
                        "action": "kick_user",
                        "user_id": user_id,
                        "client_types": client_types,
                        "reason": reason,
                    }
                }
            )
        except (TypeError, ValueError) as e:
            logger.error(f"WS pub/sub: could not serialise kick_user for {user_id}: {e}")
            return False
        try:
            await self._redis.publish(CHANNEL, body)
            return True
        except Exception as e:
            logger.error(f"WS pub/sub: kick_user publish failed: {e}", exc_info=True)
            return False

    async def _reconnect(self) -> bool:
        """Re-subscribe on the existing Redis client after a connection error.

        Closes the broken pubsub object and creates a fresh one. Does not
        close the underlying Redis client — redis.asyncio reconnects the
        transport automatically on the next command, so we only need to
        re-issue the SUBSCRIBE. Returns True on success.
        """
        logger.info("WS pub/sub: attempting reconnect…")
        await self._safe_close_pubsub()
        try:
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(CHANNEL)
            logger.info("WS pub/sub: reconnect successful")
            return True
        except Exception as e:
            logger.error(f"WS pub/sub: reconnect failed: {e}")
            await self._safe_close_pubsub()
            return False

    async def _consumer(self) -> None:
        """Long-running task that delivers incoming messages locally.

        Uses the ``get_message`` poll loop with a 1 s timeout so we can
        react to cancellation without blocking indefinitely. Consecutive
        read errors trigger a reconnect attempt so a Redis outage does not
        leave the consumer spinning against a dead pubsub object forever.
        """
        _consecutive_errors = 0
        _reconnect_threshold = 5

        try:
            while True:
                try:
                    msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    _consecutive_errors = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    _consecutive_errors += 1
                    logger.warning(
                        f"WS pub/sub: consumer read error ({_consecutive_errors}/{_reconnect_threshold}): {e}"
                    )
                    if _consecutive_errors >= _reconnect_threshold:
                        reconnected = await self._reconnect()
                        if reconnected:
                            _consecutive_errors = 0
                        else:
                            # Redis still down — back off before next probe.
                            await asyncio.sleep(5.0)
                    else:
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

                # Control envelope (B-P1-11): {"control": {"action": ...}}.
                # Distinct from the unicast/broadcast envelopes so we
                # route on shape and existing in-flight messages aren't
                # affected. kick_user is the only action today; future
                # control actions (force-reload, banner, …) extend here.
                control = data.get("control") if isinstance(data, dict) else None
                if isinstance(control, dict):
                    action = control.get("action")
                    if action == "kick_user":
                        try:
                            await self._manager.disconnect_user(
                                control.get("user_id") or "",
                                client_types=control.get("client_types"),
                                reason=control.get("reason") or "token_revoked",
                            )
                        except Exception as e:
                            logger.error(f"WS pub/sub: kick_user dispatch failed: {e}", exc_info=True)
                    else:
                        logger.warning(f"WS pub/sub: unknown control action ignored: {action}")
                    continue

                payload = data.get("message")
                if payload is None:
                    continue

                # Backwards compat: older envelopes had no "kind" field and
                # were always unicast. Treat a missing kind as unicast so a
                # rolling deploy across VMs doesn't drop in-flight messages.
                kind = data.get("kind") or "unicast"
                if kind == "broadcast":
                    prefix = data.get("prefix") or ""
                    if not prefix:
                        continue
                    try:
                        await self._manager._deliver_broadcast_local(prefix, payload)
                    except Exception as e:
                        logger.error(f"WS pub/sub: local broadcast failed for {prefix}: {e}", exc_info=True)
                    continue

                client_id = data.get("client_id")
                if not client_id:
                    continue
                try:
                    await self._manager._deliver_local(payload, client_id)
                except Exception as e:
                    # One bad delivery must not kill the consumer.
                    logger.error(f"WS pub/sub: local delivery failed for {client_id}: {e}", exc_info=True)
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
                logger.debug("ws_pubsub: stop: consumer task did not exit cleanly", exc_info=True)
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
            logger.warning("ws_pubsub: _safe_close_pubsub: unsubscribe failed", exc_info=True)
        try:
            await self._pubsub.close()
        except Exception:  # noqa: S110
            logger.warning("ws_pubsub: _safe_close_pubsub: close failed", exc_info=True)
        self._pubsub = None

    async def _safe_close_redis(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.close()
        except Exception:  # noqa: S110
            logger.warning("ws_pubsub: _safe_close_redis: close failed", exc_info=True)
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
