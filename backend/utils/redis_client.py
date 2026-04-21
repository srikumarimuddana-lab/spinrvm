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
from typing import Optional

logger = logging.getLogger(__name__)

# ── In-process fallback store ─────────────────────────────────────────────────
# Each entry: { 'value': str, 'expires_at': float | None }
_local: dict = {}

def _local_is_expired(key: str) -> bool:
    entry = _local.get(key)
    if not entry:
        return True
    exp = entry.get('expires_at')
    return exp is not None and time.monotonic() > exp

def _local_get(key: str) -> Optional[str]:
    if _local_is_expired(key):
        _local.pop(key, None)
        return None
    return _local[key]['value']

def _local_set(key: str, value: str, ttl: Optional[int] = None) -> None:
    expires_at = time.monotonic() + ttl if ttl else None
    _local[key] = {'value': value, 'expires_at': expires_at}

def _local_incr(key: str) -> int:
    if _local_is_expired(key):
        _local.pop(key, None)
    current = int(_local.get(key, {}).get('value', 0))
    new_val = current + 1
    exp = _local.get(key, {}).get('expires_at')
    _local[key] = {'value': str(new_val), 'expires_at': exp}
    return new_val

def _local_expire(key: str, ttl: int) -> None:
    if key in _local:
        _local[key]['expires_at'] = time.monotonic() + ttl

def _local_delete(key: str) -> None:
    _local.pop(key, None)

def _local_keys_matching(pattern: str) -> list:
    """Return local keys matching a simple glob pattern (only * supported)."""
    import fnmatch
    return [k for k in list(_local.keys()) if fnmatch.fnmatch(k, pattern)]

# ── Redis client (lazy init) ──────────────────────────────────────────────────
_redis = None
_redis_url: Optional[str] = None

async def _get_redis():
    global _redis, _redis_url
    url = os.environ.get('REDIS_URL', '')
    if not url:
        return None
    if _redis is not None and url == _redis_url:
        return _redis
    try:
        import redis.asyncio as aioredis  # type: ignore
        _redis = aioredis.from_url(url, encoding='utf-8', decode_responses=True)
        _redis_url = url
        logger.info(f'Redis connected: {url[:30]}...')
        return _redis
    except Exception as e:
        logger.warning(f'Redis connection failed ({e}); using in-process fallback')
        return None

# ── Public API ────────────────────────────────────────────────────────────────

async def redis_get(key: str) -> Optional[str]:
    r = await _get_redis()
    if r:
        try:
            return await r.get(key)
        except Exception as e:
            logger.warning(f'redis_get error: {e}')
    return _local_get(key)


async def redis_set(key: str, value: str, ttl: Optional[int] = None) -> None:
    r = await _get_redis()
    if r:
        try:
            if ttl:
                await r.setex(key, ttl, value)
            else:
                await r.set(key, value)
            return
        except Exception as e:
            logger.warning(f'redis_set error: {e}')
    _local_set(key, value, ttl)


async def redis_incr(key: str) -> int:
    r = await _get_redis()
    if r:
        try:
            return await r.incr(key)
        except Exception as e:
            logger.warning(f'redis_incr error: {e}')
    return _local_incr(key)


async def redis_expire(key: str, ttl: int) -> None:
    r = await _get_redis()
    if r:
        try:
            await r.expire(key, ttl)
            return
        except Exception as e:
            logger.warning(f'redis_expire error: {e}')
    _local_expire(key, ttl)


async def redis_delete(key: str) -> None:
    r = await _get_redis()
    if r:
        try:
            await r.delete(key)
            return
        except Exception as e:
            logger.warning(f'redis_delete error: {e}')
    _local_delete(key)


async def redis_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a glob pattern. Returns count deleted."""
    r = await _get_redis()
    if r:
        try:
            keys = []
            async for key in r.scan_iter(pattern):
                keys.append(key)
            if keys:
                await r.delete(*keys)
            return len(keys)
        except Exception as e:
            logger.warning(f'redis_delete_pattern error: {e}')
    # Fallback
    keys = _local_keys_matching(pattern)
    for k in keys:
        _local_delete(k)
    return len(keys)
