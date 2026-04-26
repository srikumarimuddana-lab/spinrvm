"""
Driver presence — Uber/Lyft-style "offline by default, online by proof".

A driver is considered *present* iff a short-TTL Redis key exists for them.
The key is refreshed on every WebSocket heartbeat (pong), on every status /
location update, and on WebSocket connect. When the client dies — app
killed, network cut, phone in a tunnel — the key simply expires and the
driver stops appearing in dispatch and in the admin live-monitoring view.

Contrast with the legacy ``drivers.is_online`` DB column, which stayed
True whenever a client disconnected without calling the PUT status API —
producing "ghost online" drivers nobody could dispatch to.

TTL policy
----------
``PRESENCE_TTL`` is 90 s — 3× the 30 s WebSocket heartbeat interval in
``routes/websocket.py``. That gives a driver two missed pings of grace
before they drop offline, which matches how Uber/Lyft behave on flaky
networks.

Redis transparency
------------------
All helpers go through ``utils.redis_client`` which falls back to an
in-process dict when ``REDIS_URL`` is unset. Single-replica dev works
without Redis; multi-replica prod must have Redis configured for
presence to fan out correctly.
"""

from __future__ import annotations

import logging
from typing import List, Optional

try:
    from .redis_client import (
        _get_redis,
        _local,
        redis_delete,
        redis_get,
        redis_set,
    )
except ImportError:  # pragma: no cover
    from utils.redis_client import (  # type: ignore
        _get_redis,
        _local,
        redis_delete,
        redis_get,
        redis_set,
    )

logger = logging.getLogger(__name__)

# TTL in seconds. 3× the 30 s WebSocket heartbeat — two missed pings of grace.
PRESENCE_TTL = 90

_PREFIX = "spinr:presence:driver:"


def _key(driver_id: str) -> str:
    return f"{_PREFIX}{driver_id}"


async def mark_present(driver_id: str, ttl: int = PRESENCE_TTL) -> None:
    """Refresh this driver's presence key. Idempotent; safe to call often."""
    if not driver_id:
        return
    try:
        await redis_set(_key(driver_id), "1", ttl=ttl)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[presence] mark_present({driver_id}) failed: {exc}")


async def is_present(driver_id: str) -> bool:
    """True iff a non-expired presence key exists for this driver."""
    if not driver_id:
        return False
    try:
        val = await redis_get(_key(driver_id))
        return val is not None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[presence] is_present({driver_id}) failed: {exc}")
        return False


async def clear_presence(driver_id: str) -> None:
    """Delete this driver's presence key — call on explicit sign-out / go-offline."""
    if not driver_id:
        return
    try:
        await redis_delete(_key(driver_id))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[presence] clear_presence({driver_id}) failed: {exc}")


async def present_driver_ids(candidate_ids: List[str]) -> set:
    """Return the subset of ``candidate_ids`` that are currently present.

    Batched so dispatch and admin monitoring can filter a driver pool
    without issuing N sequential round-trips. Falls back to per-key reads
    if MGET is unavailable (in-process dict fallback).
    """
    if not candidate_ids:
        return set()

    r = await _get_redis()
    if r is not None:
        try:
            keys = [_key(i) for i in candidate_ids]
            vals = await r.mget(*keys)
            return {cid for cid, v in zip(candidate_ids, vals, strict=False) if v is not None}
        except Exception as exc:
            logger.warning(f"[presence] MGET failed, falling back: {exc}")

    # In-process fallback (single replica, dev/test).
    result = set()
    for cid in candidate_ids:
        if await is_present(cid):
            result.add(cid)
    return result


async def list_present_ids() -> List[str]:
    """Return every presently-online driver id.

    Uses SCAN when Redis is connected — safe on prod — and iterates the
    in-process dict otherwise. O(total keys); intended for the sweeper
    loop and admin dashboards, NOT per-request hot paths.
    """
    r = await _get_redis()
    if r is None:
        prefix_len = len(_PREFIX)
        return [k[prefix_len:] for k in list(_local.keys()) if k.startswith(_PREFIX)]

    ids: List[str] = []
    try:
        async for key in r.scan_iter(match=f"{_PREFIX}*", count=500):
            k = key.decode() if isinstance(key, bytes) else key
            ids.append(k[len(_PREFIX) :])
    except Exception as exc:
        logger.warning(f"[presence] SCAN failed: {exc}")
    return ids


async def presence_ttl(driver_id: str) -> Optional[int]:
    """Remaining TTL in seconds, or None if no key / no Redis TTL support.

    Used by admin monitoring to show ``last_seen`` freshness without
    storing a separate timestamp.
    """
    if not driver_id:
        return None
    r = await _get_redis()
    if r is None:
        entry = _local.get(_key(driver_id))
        if not entry or entry.get("expires_at") is None:
            return None
        import time

        remaining = int(entry["expires_at"] - time.monotonic())
        return remaining if remaining > 0 else None
    try:
        ttl = await r.ttl(_key(driver_id))
        return int(ttl) if ttl and ttl > 0 else None
    except Exception as exc:  # pragma: no cover
        logger.warning(f"[presence] ttl({driver_id}) failed: {exc}")
        return None
