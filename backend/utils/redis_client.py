"""
Async Redis client with transparent in-process dict fallback.

When REDIS_URL is set the real Redis is used; when it is unset (local dev /
test) all operations fall back to an in-process dict so callers never have to
branch on Redis availability. The fallback is intentionally NOT thread-safe
for TTL expiry — it uses `time.monotonic()` for best-effort expiry only.
Production deployments must supply REDIS_URL.
"""

import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── In-process fallback store ─────────────────────────────────────────────────
# Each entry: { 'value': str, 'expires_at': float | None }
_local: dict = {}


def _local_is_expired(key: str) -> bool:
    entry = _local.get(key)
    if not entry:
        return True
    exp = entry.get("expires_at")
    return exp is not None and time.monotonic() > exp


def _local_get(key: str) -> Optional[str]:
    if _local_is_expired(key):
        _local.pop(key, None)
        return None
    return _local[key]["value"]


def _local_set(key: str, value: str, ttl: Optional[int] = None) -> None:
    expires_at = time.monotonic() + ttl if ttl else None
    _local[key] = {"value": value, "expires_at": expires_at}


def _local_incr(key: str) -> int:
    return _local_incrby(key, 1)


def _local_incrby(key: str, amount: int) -> int:
    if _local_is_expired(key):
        _local.pop(key, None)
    current = int(_local.get(key, {}).get("value", 0))
    new_val = current + amount
    exp = _local.get(key, {}).get("expires_at")
    _local[key] = {"value": str(new_val), "expires_at": exp}
    return new_val


def _local_expire(key: str, ttl: int) -> None:
    if key in _local:
        _local[key]["expires_at"] = time.monotonic() + ttl


def _local_delete(key: str) -> None:
    _local.pop(key, None)


def _local_keys_matching(pattern: str) -> list:
    """Return local keys matching a simple glob pattern (only * supported)."""
    import fnmatch

    return [k for k in list(_local.keys()) if fnmatch.fnmatch(k, pattern)]


# ── In-process fallback store: hashes ──────────────────────────────────────────
# Separate from ``_local`` (which holds plain strings) since a HASH and a
# STRING at the same key name are different Redis types — keeping them apart
# avoids a hash write silently clobbering (or being clobbered by) a string
# write to a key that happens to collide.
# Each entry: { 'fields': dict[str, str], 'expires_at': float | None }
_local_hashes: dict = {}


def _local_hash_is_expired(key: str) -> bool:
    entry = _local_hashes.get(key)
    if not entry:
        return True
    exp = entry.get("expires_at")
    return exp is not None and time.monotonic() > exp


def _local_hgetall(key: str) -> dict:
    if _local_hash_is_expired(key):
        _local_hashes.pop(key, None)
        return {}
    return dict(_local_hashes[key]["fields"])


def _local_hset(key: str, mapping: dict, ttl: Optional[int] = None) -> None:
    if _local_hash_is_expired(key):
        _local_hashes.pop(key, None)
    entry = _local_hashes.setdefault(key, {"fields": {}, "expires_at": None})
    entry["fields"].update({str(k): str(v) for k, v in mapping.items()})
    if ttl:
        entry["expires_at"] = time.monotonic() + ttl


# ── In-process fallback store: sorted sets ─────────────────────────────────────
# Each entry: { 'members': dict[str, float], 'expires_at': float | None }
_local_zsets: dict = {}


def _local_zset_is_expired(key: str) -> bool:
    entry = _local_zsets.get(key)
    if not entry:
        return True
    exp = entry.get("expires_at")
    return exp is not None and time.monotonic() > exp


def _local_zadd(key: str, mapping: dict, ttl: Optional[int] = None) -> None:
    if _local_zset_is_expired(key):
        _local_zsets.pop(key, None)
    entry = _local_zsets.setdefault(key, {"members": {}, "expires_at": None})
    entry["members"].update({str(member): float(score) for member, score in mapping.items()})
    if ttl:
        entry["expires_at"] = time.monotonic() + ttl


def _local_zrem(key: str, member: str) -> None:
    if _local_zset_is_expired(key):
        _local_zsets.pop(key, None)
        return
    _local_zsets[key]["members"].pop(str(member), None)


def _local_zrangebyscore(key: str, min_score: float) -> set:
    if _local_zset_is_expired(key):
        _local_zsets.pop(key, None)
        return set()
    return {m for m, score in _local_zsets[key]["members"].items() if score >= min_score}


# ── Redis client (lazy init) ──────────────────────────────────────────────────
_redis = None
_redis_url: Optional[str] = None


async def _get_redis():
    global _redis, _redis_url
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    if _redis is not None and url == _redis_url:
        return _redis
    try:
        import redis.asyncio as aioredis  # type: ignore

        _redis = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
        _redis_url = url
        logger.info(f"Redis connected: {url[:30]}...")
        return _redis
    except Exception as e:
        logger.warning(f"Redis connection failed ({e}); using in-process fallback")
        return None


# ── Public API ────────────────────────────────────────────────────────────────


async def redis_get(key: str) -> Optional[str]:
    r = await _get_redis()
    if r is not None:
        try:
            return await r.get(key)
        except Exception as e:
            logger.error(f"[REDIS] redis_get({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    return _local_get(key)


async def redis_mget(keys: list[str]) -> list[Optional[str]]:
    """Batch GET — one MGET round-trip instead of N redis_get calls.

    Returns a list aligned to ``keys`` (None for misses). Falls back to the
    in-process store when Redis is unset. Empty input → []. Used on the dispatch
    hot path to collapse the per-candidate offer_skip lookups into one call.
    """
    if not keys:
        return []
    r = await _get_redis()
    if r is not None:
        try:
            return await r.mget(keys)
        except Exception as e:
            logger.error(f"[REDIS] redis_mget({len(keys)} keys) failed — Redis configured but unavailable: {e}")
            raise
    return [_local_get(k) for k in keys]


async def redis_set(key: str, value: str, ttl: Optional[int] = None) -> None:
    r = await _get_redis()
    if r is not None:
        try:
            if ttl:
                await r.setex(key, ttl, value)
            else:
                await r.set(key, value)
            return
        except Exception as e:
            logger.error(f"[REDIS] redis_set({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    _local_set(key, value, ttl)


async def redis_set_nx(key: str, value: str, ttl: int) -> bool:
    """SET key value NX EX ttl — returns True iff the caller acquired the
    lock. Used by daily background loops to elect a single replica per run
    (belt-and-braces over the application logic's own idempotency), and by
    request-handler code as a dedupe/mutual-exclusion gate.

    In-process fallback: behaves the same within a single replica, so it's
    only meaningful when REDIS_URL is set in production.

    Raises on a real (Redis-configured-but-unavailable) error, matching
    every other function in this module (redis_get/set/incr/expire/delete) —
    previously this one alone caught the error and silently fell through to
    the in-process fallback with only a warning log. On a real production
    Redis blip, every replica's call independently "wins" its own local
    lock, so the "only one caller proceeds" guarantee silently became "every
    replica proceeds independently" — for a leader-election lock that means
    N replicas all doing the locked work; for a dedupe/mutual-exclusion gate
    it means N duplicate sends or a lost exclusivity guarantee, all without
    a log louder than a warning. Callers now decide explicitly how to
    degrade (see e.g. utils/scheduled_rides.py's leader lock, which fails
    open with a loud log; routes/rides/payments.py's wallet re-drive lock,
    which fails closed with a 503, since that lock IS a correctness guard)
    instead of that decision being made silently inside this function.
    """
    r = await _get_redis()
    if r is not None:
        try:
            ok = await r.set(key, value, nx=True, ex=ttl)
            return bool(ok)
        except Exception as e:
            logger.error(f"[REDIS] redis_set_nx({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    if _local_get(key) is not None:
        return False
    _local_set(key, value, ttl)
    return True


async def redis_incr(key: str) -> int:
    r = await _get_redis()
    if r is not None:
        try:
            return await r.incr(key)
        except Exception as e:
            logger.error(f"[REDIS] redis_incr({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    return _local_incr(key)


async def redis_incrby(key: str, amount: int) -> int:
    r = await _get_redis()
    if r is not None:
        try:
            return await r.incrby(key, amount)
        except Exception as e:
            logger.error(f"[REDIS] redis_incrby({key!r}, {amount!r}) failed — Redis configured but unavailable: {e}")
            raise
    return _local_incrby(key, amount)


async def redis_expire(key: str, ttl: int) -> None:
    r = await _get_redis()
    if r is not None:
        try:
            await r.expire(key, ttl)
            return
        except Exception as e:
            logger.error(f"[REDIS] redis_expire({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    _local_expire(key, ttl)


async def redis_delete(key: str) -> None:
    r = await _get_redis()
    if r is not None:
        try:
            await r.delete(key)
            return
        except Exception as e:
            logger.error(f"[REDIS] redis_delete({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    _local_delete(key)


async def redis_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a glob pattern. Returns count deleted."""
    r = await _get_redis()
    if r is not None:
        try:
            keys = []
            async for key in r.scan_iter(pattern):
                keys.append(key)
            if keys:
                await r.delete(*keys)
            return len(keys)
        except Exception as e:
            logger.error(f"[REDIS] redis_delete_pattern({pattern!r}) failed — Redis configured but unavailable: {e}")
            raise
    # Only reached when REDIS_URL is unset (dev mode).
    keys = _local_keys_matching(pattern)
    for k in keys:
        _local_delete(k)
    return len(keys)


async def redis_hgetall(key: str) -> dict:
    """HGETALL — full field/value mapping for a hash key. ``{}`` on a miss."""
    r = await _get_redis()
    if r is not None:
        try:
            return await r.hgetall(key) or {}
        except Exception as e:
            logger.error(f"[REDIS] redis_hgetall({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    return _local_hgetall(key)


async def redis_hset(key: str, mapping: dict, ttl: Optional[int] = None) -> None:
    """HSET key field value [field value ...] — merges ``mapping`` into the
    hash (existing fields not present in ``mapping`` are left untouched),
    then EXPIREs the whole key when ``ttl`` is given."""
    r = await _get_redis()
    if r is not None:
        try:
            await r.hset(key, mapping=mapping)
            if ttl:
                await r.expire(key, ttl)
            return
        except Exception as e:
            logger.error(f"[REDIS] redis_hset({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    _local_hset(key, mapping, ttl)


async def redis_zadd(key: str, mapping: dict, ttl: Optional[int] = None) -> None:
    """ZADD key score member [score member ...] — ``mapping`` is
    ``{member: score}``, then EXPIREs the whole key when ``ttl`` is given."""
    r = await _get_redis()
    if r is not None:
        try:
            await r.zadd(key, mapping)
            if ttl:
                await r.expire(key, ttl)
            return
        except Exception as e:
            logger.error(f"[REDIS] redis_zadd({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    _local_zadd(key, mapping, ttl)


async def redis_zrem(key: str, member: str) -> None:
    """ZREM key member — remove one member from a sorted set."""
    r = await _get_redis()
    if r is not None:
        try:
            await r.zrem(key, member)
            return
        except Exception as e:
            logger.error(f"[REDIS] redis_zrem({key!r}, {member!r}) failed — Redis configured but unavailable: {e}")
            raise
    _local_zrem(key, member)


async def redis_zrangebyscore_many(keys: list[str], min_score: float) -> set:
    """Union of ZRANGEBYSCORE(key, min_score, +inf) across multiple sorted-set
    keys, in one Redis round-trip via a pipeline. Empty input → set().

    Used by the H3 dispatch index to OR together every candidate cell's
    zset (drivers seen in the last INDEX_TTL_SECONDS) without N sequential
    round-trips per query.
    """
    if not keys:
        return set()
    r = await _get_redis()
    if r is not None:
        try:
            pipe = r.pipeline(transaction=False)
            for key in keys:
                pipe.zrangebyscore(key, min_score, "+inf")
            results = await pipe.execute()
        except Exception as e:
            logger.error(
                f"[REDIS] redis_zrangebyscore_many({len(keys)} keys) failed — Redis configured but unavailable: {e}"
            )
            raise
        members: set = set()
        for result in results:
            members.update(result or [])
        return members
    members = set()
    for key in keys:
        members.update(_local_zrangebyscore(key, min_score))
    return members


async def redis_eval(script: str, numkeys: int, *keys_and_args) -> Any:
    """EVAL script numkeys key [key ...] arg [arg ...] — run a Lua script.

    Raises ``RuntimeError`` when Redis is unconfigured (``REDIS_URL`` unset):
    there is no in-process Lua interpreter to fall back to, so this is the
    deliberate "not supported here" signal — callers (e.g.
    ``utils/h3_location_index.py``'s ``upsert_driver``) catch it and run an
    equivalent pure-Python path instead. Any other error (Redis configured
    but the EVAL itself failed) raises the real exception, matching every
    other function in this module.
    """
    r = await _get_redis()
    if r is None:
        raise RuntimeError("redis_eval requires REDIS_URL to be set — no local Lua interpreter")
    try:
        return await r.eval(script, numkeys, *keys_and_args)
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"[REDIS] redis_eval failed — Redis configured but unavailable: {e}")
        raise


# ── Observability helpers ─────────────────────────────────────────────────────
#
# Two functions for monitoring how much of Redis we're using:
#
#   get_redis_stats()       — O(1) / O(log N) INFO + DBSIZE. Cheap enough
#                             to expose on every /metrics scrape.
#
#   count_keys_by_prefix()  — SCAN-based per-prefix key count. O(total_keys)
#                             so it's NOT for per-request use. Call it on
#                             an admin endpoint or a once-a-minute background
#                             loop. Uses SCAN with COUNT hint so it doesn't
#                             block Redis like KEYS would.
#
# Both gracefully degrade to the in-process fallback dict so dev/test without
# REDIS_URL still returns meaningful numbers.

# Key prefixes we care about — keeps the admin dashboard rendering stable
# even when new prefixes appear. Add to this list as the app grows.
KNOWN_KEY_PREFIXES = [
    "cache:user:",  # get_user_by_id cache
    "cache:driver:",  # get_driver_by_id cache
    "cache:driver:by_user:",  # get_driver_by_user_id_cached cache
    "idem:",  # idempotency-key response cache
    "session:",  # login session lookup
    "revoked_session:",  # signed-out session tombstones (utils/session_revocation)
    "otp:",  # OTP records + lockout
    "ratelimit:",  # slowapi rate limiter
    "spinr:retry_budget:",  # per-second retry budget counter
    "spinr:ws:",  # WebSocket pub/sub channel state
    "spinr:presence:driver:",  # Uber/Lyft-style driver presence (TTL heartbeat)
    "fares:",  # per-area fare config cache
    # Registered so the ops Redis dashboard attributes these instead of
    # bucketing them into __other__ — which is exactly when you need the
    # attribution: someone staring at a memory graph trying to find the growth.
    "spinr:heatmap:",  # per-area driver heatmap payload cache
    "spinr:surge:status:",  # admin surge-status snapshot (30s)
    "spinr:locwrite:",  # driver marker write-gate window (utils/location_write_gate)
]


async def get_redis_stats() -> dict:
    """Return a summary of Redis memory/keys suitable for /metrics and
    an admin dashboard. O(1) — no SCAN — so safe to call per request.

    Shape is stable across the Redis-connected and in-process-fallback
    cases: callers can render the dict uniformly.
    """
    r = await _get_redis()
    if r is None:
        # In-process dict fallback. Best-effort memory estimation by
        # summing string sizes — good enough for a dev-mode gauge.
        total_bytes = 0
        for entry in _local.values():
            v = entry.get("value", "")
            if isinstance(v, str):
                total_bytes += len(v.encode("utf-8"))
        return {
            "backend": "in_process",
            "connected": False,
            "used_memory_bytes": total_bytes,
            "used_memory_human": _humanize_bytes(total_bytes),
            "maxmemory_bytes": 0,
            "maxmemory_human": "unlimited",
            "maxmemory_policy": "noeviction",
            "used_memory_percent": None,
            "used_memory_peak_bytes": total_bytes,
            "total_keys": len(_local),
            "keyspace_hits_total": None,
            "keyspace_misses_total": None,
            "evicted_keys_total": 0,
            "expired_keys_total": 0,
            "total_commands_processed": None,
            "connected_clients": 1,
            "uptime_seconds": None,
        }

    try:
        info_mem = await r.info("memory")
        info_stats = await r.info("stats")
        info_clients = await r.info("clients")
        info_server = await r.info("server")
        dbsize = await r.dbsize()
    except Exception as exc:
        logger.warning(f"[REDIS] INFO failed: {exc}")
        return {"backend": "redis", "connected": False, "error": str(exc)}

    used = int(info_mem.get("used_memory", 0) or 0)
    maxmem = int(info_mem.get("maxmemory", 0) or 0)
    percent = (used / maxmem * 100) if maxmem > 0 else None

    return {
        "backend": "redis",
        "connected": True,
        "used_memory_bytes": used,
        "used_memory_human": info_mem.get("used_memory_human", _humanize_bytes(used)),
        "maxmemory_bytes": maxmem,
        "maxmemory_human": info_mem.get("maxmemory_human", "unlimited" if maxmem == 0 else _humanize_bytes(maxmem)),
        "maxmemory_policy": info_mem.get("maxmemory_policy", "noeviction"),
        "used_memory_percent": percent,
        "used_memory_peak_bytes": int(info_mem.get("used_memory_peak", 0) or 0),
        "total_keys": int(dbsize or 0),
        "keyspace_hits_total": int(info_stats.get("keyspace_hits", 0) or 0),
        "keyspace_misses_total": int(info_stats.get("keyspace_misses", 0) or 0),
        "evicted_keys_total": int(info_stats.get("evicted_keys", 0) or 0),
        "expired_keys_total": int(info_stats.get("expired_keys", 0) or 0),
        "total_commands_processed": int(info_stats.get("total_commands_processed", 0) or 0),
        "connected_clients": int(info_clients.get("connected_clients", 0) or 0),
        "uptime_seconds": int(info_server.get("uptime_in_seconds", 0) or 0),
    }


async def count_keys_by_prefix(prefixes: Optional[list] = None, scan_count: int = 500) -> dict:
    """Count keys under each prefix. Uses SCAN so it's safe on prod,
    but it's O(total_keys) — call on admin endpoints or a slow loop,
    NOT per request.

    Returns `{prefix: count}` with every supplied prefix present
    (including zeroes) so the dashboard can render a stable row set.
    Unmatched keys are totaled under the pseudo-prefix "__other__".
    """
    target_prefixes = list(prefixes) if prefixes is not None else list(KNOWN_KEY_PREFIXES)
    counts: dict = {p: 0 for p in target_prefixes}
    counts["__other__"] = 0

    r = await _get_redis()
    if r is None:
        for k in _local.keys():
            matched = False
            for p in target_prefixes:
                if k.startswith(p):
                    counts[p] += 1
                    matched = True
                    break
            if not matched:
                counts["__other__"] += 1
        return counts

    try:
        async for key in r.scan_iter(match="*", count=scan_count):
            k = key.decode() if isinstance(key, bytes) else key
            matched = False
            for p in target_prefixes:
                if k.startswith(p):
                    counts[p] += 1
                    matched = True
                    break
            if not matched:
                counts["__other__"] += 1
    except Exception as exc:
        logger.warning(f"[REDIS] SCAN for prefix counts failed: {exc}")

    return counts


def _humanize_bytes(n: int) -> str:
    """Format bytes as a short human string (e.g. 12.3M, 2.1G)."""
    if n < 0:
        return "0B"
    units = ["B", "K", "M", "G", "T"]
    size = float(n)
    unit = units[0]
    for u in units:
        if size < 1024:
            unit = u
            break
        size /= 1024
    if unit == "B":
        return f"{int(size)}{unit}"
    return f"{size:.1f}{unit}"


def loop_pod_id() -> str:
    """Stable-per-process identifier for leader-lock ownership.

    Only ever read back diagnostically — SET NX decides the winner, not this
    value — but a real id makes "which replica holds it" answerable from a
    redis-cli GET during an incident.
    """
    import socket

    return f"{socket.gethostname()}:{os.getpid()}"


async def try_acquire_leader_lock(name: str, ttl_seconds: int, *, logger_=None) -> bool:
    """Try to become the single replica running this tick. Fails OPEN.

    A convenience wrapper over ``redis_set_nx`` for the background loops that
    take a leader lock purely to shed DB load — every caller of this helper is
    ALREADY replay-safe by atomic claim or idempotency key, so the lock is an
    optimisation, never a correctness guard. A loop whose correctness depends
    on single-execution must not use this: it fails open, so on a Redis error
    every replica proceeds, exactly as it would with no lock at all.

    TTL RULE (load-bearing): ``ttl_seconds`` must be SHORTER than the minimum
    possible sleep between ticks, jitter included. Otherwise the pod that ran
    the last tick wakes to find its OWN key still alive, fails SET NX, and
    sleeps another full interval — silently halving the loop's cadence.
    ``interval * 0.85`` against a ``* 0.9`` jitter floor is the established
    formula (see offer_expiry_reaper.py and ledger_projection.py).

    With REDIS_URL unset, redis_client falls back to an in-process dict, so
    every replica wins its own local lock and the loop behaves exactly as it
    did before the lock existed. That is the intended degradation.
    """
    log = logger_ or logger
    # Floor at 1s. A caller computing `int(interval * 0.85)` from a sub-2s
    # interval lands on 0, and a 0/negative TTL is degenerate: depending on the
    # backend it either expires instantly (no lock at all) or never expires (the
    # loop deadlocks itself, skipping every tick after the first). Neither is
    # what a caller asking for a lock means.
    ttl_seconds = max(1, int(ttl_seconds))
    try:
        return await redis_set_nx(f"spinr:{name}:lock", loop_pod_id(), ttl_seconds)
    except Exception as lock_err:
        log.error("%s: leader lock unavailable (%s), proceeding without it", name, lock_err)
        return True
