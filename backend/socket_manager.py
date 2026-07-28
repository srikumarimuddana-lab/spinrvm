from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import WebSocket
from loguru import logger

# B-P3-1: per-message timeout for fan-out broadcasts. Prevents a single
# stuck socket from stalling the whole iteration (TCP half-close + kernel
# keepalive can take tens of seconds otherwise).
_BROADCAST_SEND_TIMEOUT = 2.0

# #3: min seconds between admin location-fan-out messages per driver.
_ADMIN_LOC_MIN_INTERVAL = 3.0

try:
    from .logging_utils import diag_logger  # type: ignore
    from .utils.metrics import observe as _metric_observe  # type: ignore
    from .utils.redis_client import redis_expire, redis_incr  # type: ignore
except ImportError:
    from logging_utils import diag_logger  # type: ignore
    from utils.metrics import observe as _metric_observe  # type: ignore
    from utils.redis_client import redis_expire, redis_incr  # type: ignore


# B-P1-12 / B4: per-user inbound message rate-limit, enforced fleet-wide.
# The receive loop in routes/websocket.py used to enforce a per-CONNECTION
# cap (30 msg/s via a closure-scoped timestamp list), which an attacker
# could side-step by opening N sockets to get N×30 effective throughput.
# That was fixed by aggregating per-user on this machine — but a user
# force-balanced across replicas (or simply landing on different
# machines across reconnects) still got up to ``replica_count × cap``
# throughput, since each replica's ``ConnectionManager`` held its own
# in-process bucket.
#
# note_user_message now enforces the cap via a Redis fixed-window
# counter (INCR + EXPIRE 1s) keyed on user_id, shared across every
# replica — ``utils/redis_client.py`` transparently falls back to an
# in-process dict when REDIS_URL is unset (dev/test), so this degrades
# to the old per-machine behaviour there with no code branching needed.
# If Redis IS configured but a call fails (network blip, Redis down),
# we fail OPEN to the original per-machine sliding-window bucket below
# rather than blocking every WS message fleet-wide on a transient Redis
# hiccup — matching the non-security-critical fail-open precedent in
# ``utils/rate_limiter.py``'s ``RedisRateLimiter`` (OTP keys fail closed;
# general rate limits degrade to memory).
WS_MAX_MESSAGES_PER_SECOND_PER_USER = 30


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        # B-P1-12: per-user inbound msg timestamps. See module-level
        # comment above for the cross-machine caveat.
        self._user_msg_timestamps: Dict[str, List[float]] = {}
        # #3: per-driver throttle for the high-frequency admin location fan-out.
        # A driver's pings land on one replica, so an in-process gate here caps
        # the publish rate to once per interval per driver.
        self._admin_loc_last: Dict[str, float] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        # WebSocket is already accepted in the endpoint handler
        self.active_connections[client_id] = websocket
        logger.info(f"WebSocket connected: {client_id}")
        diag_logger.info(
            f"[WS] CONNECT client_id={client_id} "
            f"total_connections={len(self.active_connections)} "
            f"all_keys={list(self.active_connections.keys())}"
        )

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        logger.info(f"WebSocket disconnected: {client_id}")
        diag_logger.info(
            f"[WS] DISCONNECT client_id={client_id} "
            f"remaining={len(self.active_connections)} "
            f"all_keys={list(self.active_connections.keys())}"
        )
        # B-P1-12: drop the user's rate-limit bucket if this was their
        # last socket on this machine. Without this the dict would grow
        # unbounded as users come and go (16 bytes per stale empty list,
        # so not catastrophic, but trivial to bound here).
        user_id = self._user_id_from_key(client_id)
        if user_id is not None:
            self._maybe_drop_user_bucket(user_id)

    @staticmethod
    def _user_id_from_key(client_id: str) -> Optional[str]:
        """Parse the ``user_id`` out of a ``{client_type}_{user_id}`` key.
        Returns None if the key doesn't match the expected shape — defensive
        only; in practice every key in active_connections is built via
        ``f"{client_type}_{user['id']}"`` in routes/websocket.py."""
        for prefix in ("rider_", "driver_", "admin_"):
            if client_id.startswith(prefix):
                return client_id[len(prefix) :]
        return None

    def _has_sockets_for_user(self, user_id: str) -> bool:
        """True iff at least one ``{rider,driver,admin}_{user_id}`` key
        is in active_connections. Used to decide whether to evict the
        per-user msg bucket on disconnect."""
        for ct in ("rider", "driver", "admin"):
            if f"{ct}_{user_id}" in self.active_connections:
                return True
        return False

    def _maybe_drop_user_bucket(self, user_id: str) -> None:
        if not self._has_sockets_for_user(user_id):
            self._user_msg_timestamps.pop(user_id, None)

    def connection_stats(self) -> Dict[str, int]:
        """Count active sockets on THIS replica, bucketed by client type.

        Keys are ``{client_type}_{user_id}`` (see routes/websocket.py), so we
        bucket on the prefix. With multiple uvicorn workers each process holds
        its own ``active_connections`` dict, so these counts are per-replica,
        not fleet-wide — the admin WS-health card labels them that way.
        """
        counts = {"total": 0, "admins": 0, "drivers": 0, "riders": 0}
        for key in self.active_connections:
            counts["total"] += 1
            if key.startswith("admin_"):
                counts["admins"] += 1
            elif key.startswith("driver_"):
                counts["drivers"] += 1
            elif key.startswith("rider_"):
                counts["riders"] += 1
        return counts

    async def note_user_message(
        self,
        user_id: str,
        *,
        max_per_second: int = WS_MAX_MESSAGES_PER_SECOND_PER_USER,
    ) -> bool:
        """Record an inbound WebSocket message for ``user_id`` against
        the per-user rate limit (B-P1-12 / B4), enforced fleet-wide.

        Returns True if the message is within budget; False if the user
        has exceeded ``max_per_second`` messages over the current 1-second
        window across every socket they have open, on every replica. The
        caller (routes/websocket.py receive loop) emits a typed
        ``rate_limited`` frame on False and drops the message — the
        socket is NOT closed, matching the prior per-connection
        behaviour so a brief burst doesn't tear down a healthy session.

        Primary path: a Redis fixed-window counter (``INCR`` then
        ``EXPIRE 1`` on the first increment of each window), keyed on
        user_id — shared across every replica. ``utils/redis_client.py``
        transparently falls back to an in-process dict when REDIS_URL is
        unset, so local/dev/test behave identically without branching
        here. If Redis IS configured but the call raises (network blip,
        Redis down), we fail OPEN to ``_note_user_message_local`` — the
        original per-machine sliding-window bucket — rather than
        blocking every WS message fleet-wide on a transient Redis
        hiccup.
        """
        key = f"ws:msgrate:{user_id}"
        try:
            count = await redis_incr(key)
            if count == 1:
                await redis_expire(key, 1)
            return count <= max_per_second
        except Exception as e:
            logger.warning(
                f"WS rate limiter: Redis unavailable for user_id={user_id}, degrading to per-machine fallback: {e}"
            )
            return self._note_user_message_local(user_id, max_per_second)

    def _note_user_message_local(self, user_id: str, max_per_second: int) -> bool:
        """Per-machine sliding-window fallback used only when the Redis
        call in ``note_user_message`` raises (Redis configured but
        unreachable). Same algorithm B-P1-12 originally shipped with —
        see ``_user_msg_timestamps`` and the bucket-cleanup methods
        below, which exist solely to bound this fallback's memory under
        churn (the Redis path needs no such cleanup; its keys expire on
        their own).
        """
        now_ts = time.monotonic()
        cutoff = now_ts - 1.0
        bucket = self._user_msg_timestamps.get(user_id)
        if bucket is None:
            bucket = []
            self._user_msg_timestamps[user_id] = bucket
        bucket[:] = [t for t in bucket if t >= cutoff]
        if len(bucket) >= max_per_second:
            return False
        bucket.append(now_ts)
        return True

    async def disconnect_user(
        self,
        user_id: str,
        *,
        client_types: Optional[List[str]] = None,
        reason: str = "token_revoked",
    ) -> int:
        """Force-close every LOCAL WebSocket whose key matches user_id (B-P1-11).

        Returns the number of sockets actually closed. ``client_types``
        defaults to {rider, driver, admin}; pass a narrower list to
        scope the kick (e.g. ["rider", "driver"] for /auth/logout-all,
        ["admin"] for /admin/auth/logout-all).

        This only touches sockets on THIS machine. For multi-replica
        deployments the caller should route through ``kick_user`` (or
        ``ws_pubsub.publish_kick_user``) so every VM gets the signal.

        We try to send a final ``session_revoked`` frame before closing
        so the client can surface "you signed out everywhere" rather
        than a generic disconnect — best-effort only; a dead socket
        will raise on send and we proceed to close anyway.
        """
        types: List[str] = list(client_types) if client_types else ["rider", "driver", "admin"]
        closed = 0
        for client_type in types:
            key = f"{client_type}_{user_id}"
            ws = self.active_connections.get(key)
            if ws is None:
                continue
            try:
                await ws.send_json({"type": "session_revoked", "reason": reason})
            except Exception:  # noqa: S110 — socket may already be dead
                pass
            try:
                # 1008 = "policy violation" per RFC 6455. The reason
                # string is capped at 123 bytes by the spec; trim
                # defensively so we never raise a ProtocolError when
                # the caller passes something verbose.
                await ws.close(code=1008, reason=(reason or "")[:120])
            except Exception as e:
                logger.warning(f"disconnect_user: close failed for {key}: {e}")
            # Pop unconditionally — the socket is unusable either way,
            # and a stale entry would let send_personal_message try to
            # write to a dead handle.
            self.active_connections.pop(key, None)

            closed += 1
        if closed:
            logger.info(f"disconnect_user: kicked {closed} local socket(s) for user_id={user_id} reason={reason}")
        # B-P1-12: drop the user's rate-limit bucket if no sockets remain
        # for this user on this machine. ``client_types`` may have been
        # narrower than the user's full set (e.g. an admin logout that
        # only kicks admin sockets), so re-check rather than blindly pop.
        self._maybe_drop_user_bucket(user_id)
        return closed

    async def kick_user(
        self,
        user_id: str,
        *,
        client_types: Optional[List[str]] = None,
        reason: str = "token_revoked",
    ) -> int:
        """Force-disconnect every WS for ``user_id`` across the fleet (B-P1-11).

        Cross-replica: best-effort fan-out via Redis pub/sub when active.
        Local: always runs (idempotent if pub/sub loops back to us).

        Returns the local-machine kick count. Remote kicks happen
        asynchronously on the receiving replicas and are not
        enumerable from here — that's by design, the caller doesn't
        need to wait for every machine.
        """
        types_list: Optional[List[str]] = list(client_types) if client_types else None
        try:
            from utils.ws_pubsub import pubsub
        except ImportError:  # pragma: no cover — package-relative fallback
            from .utils.ws_pubsub import pubsub  # type: ignore
        # Fire the cross-replica signal first so other VMs start their
        # disconnect work in parallel with our local close. publish
        # is itself best-effort; a False return just means single-machine
        # mode, which is fine because the local close below covers us.
        try:
            await pubsub.publish_kick_user(
                user_id,
                client_types=types_list,
                reason=reason,
            )
        except Exception as e:
            logger.warning(f"kick_user: pub/sub publish failed for {user_id}: {e}")
        return await self.disconnect_user(
            user_id,
            client_types=types_list,
            reason=reason,
        )

    async def send_personal_message(self, message: dict, client_id: str, *, durable: bool = True):
        """Send a message to a client, possibly on a different machine.

        ``durable=False`` marks the message as ephemeral: it skips the
        per-client seq/outbox replay ring (see ws_pubsub.publish) so 1 Hz
        location fan-out can't evict the ride events that ``?last_seq``
        reconnect recovery replays. Use for messages the next tick
        supersedes; everything ride-state-related stays durable.

        When ``ws_pubsub`` is active (production / multi-machine) the
        message goes onto a shared Redis channel; every machine's
        subscriber receives it and delivers iff the client is in its
        local dict. That includes THIS machine — so we must NOT also
        call ``_deliver_local`` here, or local connections would
        receive the message twice. The Redis round-trip for same-VM
        delivery is sub-millisecond and buys us correctness across the
        fleet.

        When pub/sub is disabled (dev / degraded Redis) we fall back
        to direct local delivery — which is exactly the pre-P0-B3
        behaviour.
        """
        try:
            from utils.ws_pubsub import pubsub
        except ImportError:  # pragma: no cover — package-relative fallback
            from .utils.ws_pubsub import pubsub  # type: ignore

        # KPI: spinr_ws_fanout_duration_ms (P95 < 100ms SLA). The pubsub
        # label measures publish-to-Redis (delivery completes on the
        # subscriber's _deliver_local); local measures the actual socket send.
        t0 = time.monotonic()
        if await pubsub.publish(client_id, message, durable=durable):
            _metric_observe("spinr_ws_fanout_duration_ms", (time.monotonic() - t0) * 1000.0, {"path": "pubsub"})
            return
        await self._deliver_local(message, client_id)
        _metric_observe("spinr_ws_fanout_duration_ms", (time.monotonic() - t0) * 1000.0, {"path": "local"})

    async def _deliver_local(self, message: dict, client_id: str):
        """Write ``message`` to the socket for ``client_id`` on THIS machine only.

        Called by both the direct-send fallback and by the Redis pub/sub
        subscriber. Keeping it as a single method means diagnostics and
        error handling stay consistent regardless of which path
        triggered the delivery.
        """
        msg_type = message.get("type", "?") if isinstance(message, dict) else "?"
        if client_id in self.active_connections:
            try:
                # B-P3-1 (unicast): same per-message deadline as broadcast().
                # The Redis pub/sub consumer delivers serially, so a single
                # half-closed socket blocking send_json until the kernel
                # keepalive fires (~tens of seconds) would queue every offer /
                # ride_taken / status change on this replica behind it,
                # breaching the <100ms fan-out and <2s dispatch SLAs.
                await asyncio.wait_for(
                    self.active_connections[client_id].send_json(message),
                    timeout=_BROADCAST_SEND_TIMEOUT,
                )
                diag_logger.info(f"[WS] SEND ok client_id={client_id} type={msg_type}")
            except asyncio.TimeoutError:
                logger.warning(
                    f"_deliver_local: send timed out after {_BROADCAST_SEND_TIMEOUT}s "
                    f"client_id={client_id} type={msg_type}; skipping (heartbeat will reap the socket)"
                )
                diag_logger.info(f"[WS] SEND TIMEOUT client_id={client_id} type={msg_type}")
            except Exception as e:
                diag_logger.info(f"[WS] SEND FAILED client_id={client_id} type={msg_type} err={e}")
        else:
            # In multi-machine mode this is expected for every message
            # whose target happens to live on another VM — don't treat
            # it as a drop unless we're the only machine serving the
            # fleet.
            diag_logger.debug(
                f"[WS] not local client_id={client_id} type={msg_type} "
                f"(message may have been delivered by another machine)"
            )

    async def broadcast(self, message: dict):
        """Broadcast to every socket connected to THIS machine.

        Intentionally local-only: broadcast() has no current callers
        outside of legacy test paths, and an unscoped cross-machine
        broadcast would need a story for excluding admin-only channels.
        Prefer prefix-scoped broadcasts (``broadcast_to_admins``) which
        fan out correctly across VMs.

        B-P3-1: per-message 2 s timeout so a single stuck socket can't
        stall the whole broadcast (FastAPI's WebSocket.send_json await
        has no built-in deadline; a half-closed TCP connection blocks
        until the kernel keepalive kicks in, ~tens of seconds).
        """
        connections = list(self.active_connections.values())  # snapshot to avoid mutation during iteration
        for connection in connections:
            try:
                await asyncio.wait_for(connection.send_json(message), timeout=_BROADCAST_SEND_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(f"broadcast: send timed out after {_BROADCAST_SEND_TIMEOUT}s; skipping connection")
            except Exception as e:
                logger.warning(f"broadcast: send failed: {e}")

    async def broadcast_to_admins(self, message: dict):
        """Broadcast to every admin client across the fleet.

        Fans out via Redis pub/sub so admins on other VMs receive the
        message — previously this was local-only, so an admin connected
        to VM-A would miss a driver_status_changed event triggered on
        VM-B. When pub/sub is down we fall back to local delivery,
        which matches the pre-change behaviour.
        """
        try:
            from utils.ws_pubsub import pubsub
        except ImportError:  # pragma: no cover — package-relative fallback
            from .utils.ws_pubsub import pubsub  # type: ignore

        if await pubsub.publish_broadcast("admin_", message):
            return
        await self._deliver_broadcast_local("admin_", message)

    async def broadcast_driver_location_to_admins(self, driver_id: str, message: dict) -> None:
        """Throttled admin fan-out for driver location pings (#3).

        Ride-status events go through broadcast_to_admins unthrottled; only the
        ~1 Hz location pings are rate-limited (admins don't need sub-interval
        granularity), cutting the N-drivers x A-admins x 1 Hz fan-out down to
        once per driver per interval. NOTE: this is the load-shedding half —
        viewport/bbox filtering (only sending drivers an admin is actually
        looking at) needs an admin-client subscription protocol and is tracked
        as a follow-up.
        """
        now = time.monotonic()
        if now - self._admin_loc_last.get(driver_id, 0.0) < _ADMIN_LOC_MIN_INTERVAL:
            return
        self._admin_loc_last[driver_id] = now
        await self.broadcast_to_admins(message)

    def forget_driver_location_throttle(self, driver_id: str) -> None:
        """Drop a driver's admin-location throttle entry (P7). Called on driver
        disconnect so _admin_loc_last doesn't retain one float per driver ever
        seen (an unbounded, if slow, leak). Location pings for a driver land on
        one replica, so this local eviction is sufficient."""
        self._admin_loc_last.pop(driver_id, None)

    async def broadcast_ride_status(
        self,
        ride_id: str,
        status: str,
        rider_id: str | None = None,
        driver_user_id: str | None = None,
        version: int | None = None,
        **extra,
    ):
        """Emit a unified ``ride_status_changed`` event for a ride transition.

        Rider app, driver app, and admin dashboard all listen for this single
        event type and switch on ``status``. Individual specific events
        (``driver_accepted``, ``ride_started``, …) still fire for
        backward-compat, but every backend state transition should also
        call this so live-monitoring stays consistent without per-event wiring.

        ``rider_id`` and ``driver_user_id`` are optional — pass None for
        connections that shouldn't receive the event (e.g. driver_user_id=None
        for transitions the driver triggers themselves and already knows about).

        ``version`` is the ride's monotonic event version (rides.version, bumped
        by the migration-225 trigger on every UPDATE). Pass the value from the
        post-update rides row so clients can drop stale / out-of-order events by
        comparing it against the highest they've applied. It is stamped
        authoritatively (wins over any ``version`` in ``extra``) and OMITTED when
        None — a caller with no version must not emit ``"version": null``, which
        a client cannot tell apart from version 0.
        """
        payload = {"type": "ride_status_changed", "ride_id": ride_id, "status": status, **extra}
        if version is not None:
            payload["version"] = version
        if rider_id:
            try:
                await self.send_personal_message(payload, f"rider_{rider_id}")
            except Exception as e:
                logger.warning(f"broadcast_ride_status: rider send failed for {rider_id}: {e}")
        if driver_user_id:
            try:
                await self.send_personal_message(payload, f"driver_{driver_user_id}")
            except Exception as e:
                logger.warning(f"broadcast_ride_status: driver send failed for {driver_user_id}: {e}")
        try:
            await self.broadcast_to_admins(payload)
        except Exception as e:
            logger.warning(f"broadcast_ride_status: admin broadcast failed for {ride_id}: {e}")

    async def _deliver_broadcast_local(self, prefix: str, message: dict):
        """Deliver ``message`` to every local socket whose key starts with
        ``prefix``. Snapshot the match list before iterating so a
        concurrent disconnect doesn't RuntimeError the loop.

        B-P3-1: same per-message timeout as broadcast() — a stuck admin
        client must not stall the entire admin fan-out.

        Sends are dispatched CONCURRENTLY (asyncio.gather): a slow/half-open
        admin socket must only cost its own per-socket timeout, not serialise
        the whole fleet's fan-out (with A admins, sequential awaits made
        worst-case latency A × timeout). Each send owns its timeout + error
        handling so one failure never affects the others.
        """
        keys = [k for k in self.active_connections if k.startswith(prefix)]

        async def _send_one(key: str) -> None:
            ws = self.active_connections.get(key)
            if ws is None:
                return
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=_BROADCAST_SEND_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(f"Failed to send to {key}: timed out after {_BROADCAST_SEND_TIMEOUT}s")
            except Exception as e:
                logger.warning(f"Failed to send to {key}: {e}")

        if keys:
            await asyncio.gather(*(_send_one(key) for key in keys))

    async def update_driver_location(self, driver_id: str, lat: float, lng: float):
        try:
            from utils.redis_client import redis_set
        except ImportError:
            from .utils.redis_client import redis_set  # type: ignore

        import json

        payload = json.dumps(
            {
                "lat": lat,
                "lng": lng,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        # This is an ephemeral 60 s cache (rider-map fast path); the
        # authoritative location lives in the drivers table. A Redis outage
        # here is degraded-but-recovered — log a warning and move on rather
        # than letting redis_set's re-raise propagate. Letting it raise once
        # blocked the caller's downstream DB write of the same coordinates,
        # so a Redis blip left drivers with no persisted position and the
        # admin live-monitoring map drew no car marker for an online driver.
        try:
            await redis_set(f"spinr:driver:location:{driver_id}", payload, ttl=60)
        except Exception as e:
            logger.warning(f"driver location cache write failed (driver={driver_id}): {e}")

    async def get_driver_location(self, driver_id: str) -> Optional[Dict]:
        try:
            from utils.redis_client import redis_get
        except ImportError:
            from .utils.redis_client import redis_get  # type: ignore

        import json

        data = await redis_get(f"spinr:driver:location:{driver_id}")
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                pass
        return None


manager = ConnectionManager()
