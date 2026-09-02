"""Redis H3 live-location index for dispatch candidate lookup.

Model
-----
- ``spinr:h3:rev:{driver_id}`` — HASH ``ts, 7, 8, 9`` (last-write epoch + cells)
- ``spinr:h3:cell:{res}:{h3index}`` — ZSET of driver IDs scored by last-seen unix ts
- ``spinr:h3:ready`` — generation + rebuild metadata; missing ⇒ unready
- ``spinr:h3:unhealthy`` — sticky flag until a *complete* rebuild clears it
- ``spinr:h3:events`` — last 50 health/failover events for the admin dashboard
- ``spinr:h3:writes_enabled`` — ``1`` when global or any area is ``h3``/``shadow``

The index is **not authoritative**. Matching re-reads Postgres and reapplies
eligibility (online/available/verified/WAV/area). An incomplete Redis view
must never be treated as "no drivers" — callers check :func:`is_ready`
and fall back to PostGIS/legacy.

Writes are fail-open for the GPS path (a Redis blip must not block the
``drivers.lat/lng`` update) and fail-closed for readiness (the same blip
marks the index unhealthy so dispatch does not serve a holey ring).

GPS/presence writes stay dark until ``dispatch_geo_provider`` is ``h3`` or
``shadow`` (or a service-area override is). In-process Redis fallback
(``REDIS_URL`` unset) is never treated as ready.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from .h3_cells import (
        DEFAULT_DISPATCH_RESOLUTION,
        cells_covering,
        cells_for_all_resolutions,
        resolution_for_query,
    )
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
    from .redis_client import (
        get_redis_stats,
        redis_delete,
        redis_eval,
        redis_get,
        redis_hgetall,
        redis_hset,
        redis_set,
        redis_set_nx,
        redis_zadd,
        redis_zrangebyscore_many,
        redis_zrem,
    )
except ImportError:  # pragma: no cover
    from utils.h3_cells import (  # type: ignore
        DEFAULT_DISPATCH_RESOLUTION,
        cells_covering,
        cells_for_all_resolutions,
        resolution_for_query,
    )
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import set_gauge as _metric_gauge  # type: ignore
    from utils.redis_client import (  # type: ignore
        get_redis_stats,
        redis_delete,
        redis_eval,
        redis_get,
        redis_hgetall,
        redis_hset,
        redis_set,
        redis_set_nx,
        redis_zadd,
        redis_zrangebyscore_many,
        redis_zrem,
    )

logger = logging.getLogger(__name__)

# Longer than presence (30s) so a missed ping does not drop the driver from
# the index before the next location write. Matching still presence-filters.
INDEX_TTL_SECONDS = 90
MAX_EVENTS = 50
# Activation requires this exact policy. Unknown / volatile-* / allkeys-* block.
REQUIRED_EVICTION_POLICY = "noeviction"
FORBIDDEN_EVICTION_POLICIES = frozenset(
    {
        "allkeys-lru",
        "allkeys-lfu",
        "allkeys-random",
        "volatile-lru",
        "volatile-lfu",
        "volatile-random",
        "volatile-ttl",
    }
)
MEMORY_HEADROOM_PERCENT = 60.0
REDIS_STATS_CACHE_SECONDS = 15.0

REV_PREFIX = "spinr:h3:rev:"
CELL_PREFIX = "spinr:h3:cell:"
READY_KEY = "spinr:h3:ready"
UNHEALTHY_KEY = "spinr:h3:unhealthy"
EVENTS_KEY = "spinr:h3:events"
LAST_SERVED_KEY = "spinr:h3:last_served"
NOTIFY_PREFIX = "spinr:h3:notify:"
WRITES_KEY = "spinr:h3:writes_enabled"
LAST_SERVED_TTL_SECONDS = 900
NOTIFY_DEDUP_SECONDS = 60
_SENTRY_KINDS = frozenset({"failover", "unhealthy", "shadow_error"})

# Process-local fallbacks used when Redis itself is the incident (SET NX
# would otherwise fail on every ride and re-flood Sentry/WS).
_local_notify_until: dict[str, float] = {}
_local_last_served_slots: dict[str, dict[str, Any]] = {}
_redis_stats_cache: Optional[tuple[float, dict[str, Any]]] = None

_RESOLUTIONS = (7, 8, 9)

# Atomic move + stale-ts reject. HASH fields only — no cjson (Upstash).
# Returns 1 on write, 0 if the stored ts is newer than ARGV[1].
_UPSERT_LUA = """
local ts = tonumber(ARGV[1])
local old_ts = tonumber(redis.call('HGET', KEYS[1], 'ts') or '0')
if old_ts ~= nil and old_ts > ts then
  return 0
end
local driver = ARGV[2]
local ttl = tonumber(ARGV[3])
local prefix = ARGV[4]
local ress = {'7', '8', '9'}
local newc = {ARGV[5], ARGV[6], ARGV[7]}
for i = 1, 3 do
  local prev = redis.call('HGET', KEYS[1], ress[i])
  if prev and prev ~= '' and prev ~= newc[i] then
    redis.call('ZREM', prefix .. ress[i] .. ':' .. prev, driver)
  end
  if newc[i] and newc[i] ~= '' then
    local ck = prefix .. ress[i] .. ':' .. newc[i]
    redis.call('ZADD', ck, ts, driver)
    redis.call('EXPIRE', ck, ttl)
  end
end
redis.call('HSET', KEYS[1], 'ts', ARGV[1], '7', ARGV[5], '8', ARGV[6], '9', ARGV[7])
redis.call('EXPIRE', KEYS[1], ttl)
return 1
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cell_key(res: int, cell: str) -> str:
    return f"{CELL_PREFIX}{res}:{cell}"


def _rev_key(driver_id: str) -> str:
    return f"{REV_PREFIX}{driver_id}"


def _area_slot(area_id: Optional[str]) -> str:
    return str(area_id) if area_id else "_"


async def mark_unhealthy(reason: str, *, extra: Optional[dict] = None) -> None:
    """Fail-closed: dispatch must not use H3 until a rebuild proves completeness.

    The flag is sticky (no TTL). A 120s expiry used to let a failed rebuild
    look healthy without a successful complete pass.
    """
    payload = {"reason": reason, "at": _now_iso(), **(extra or {})}
    try:
        await redis_set(UNHEALTHY_KEY, json.dumps(payload))
    except Exception as exc:
        logger.error("h3 mark_unhealthy redis_set failed: %s", exc, exc_info=True)
    _metric_inc("spinr_dispatch_h3_unhealthy_total", {"reason": reason[:48]})
    await record_event("unhealthy", reason=reason, extra=extra)
    await notify_dispatch_geo_ops(
        {
            "kind": "unhealthy",
            "reason": reason,
            "severity": "critical",
            **{k: v for k, v in (extra or {}).items() if k not in {"lat", "lng"}},
        }
    )


async def clear_unhealthy() -> None:
    try:
        await redis_delete(UNHEALTHY_KEY)
    except Exception as exc:
        logger.error("h3 clear_unhealthy failed: %s", exc, exc_info=True)


async def record_event(kind: str, *, reason: str = "", extra: Optional[dict] = None) -> None:
    """Append a dashboard-visible event. Never includes lat/lng (PIPEDA)."""
    event = {
        "kind": kind,
        "reason": reason,
        "at": _now_iso(),
        **{k: v for k, v in (extra or {}).items() if k not in {"lat", "lng", "pickup_lat", "pickup_lng"}},
    }
    try:
        raw = await redis_get(EVENTS_KEY)
        events = json.loads(raw) if raw else []
        if not isinstance(events, list):
            events = []
        events.insert(0, event)
        await redis_set(EVENTS_KEY, json.dumps(events[:MAX_EVENTS]))
    except Exception as exc:
        logger.error("h3 record_event failed: %s", exc, exc_info=True)


async def recent_events(limit: int = 20) -> list[dict]:
    try:
        raw = await redis_get(EVENTS_KEY)
        events = json.loads(raw) if raw else []
        if not isinstance(events, list):
            return []
        return events[: max(1, min(int(limit), MAX_EVENTS))]
    except Exception as exc:
        logger.error("h3 recent_events failed: %s", exc, exc_info=True)
        return []


def _notify_fingerprint(event: dict) -> str:
    return "|".join(str(event.get(k) or "")[:48] for k in ("kind", "from_provider", "to_provider", "reason"))[:120]


async def should_broadcast_ops(fingerprint: str) -> bool:
    """True on the first occurrence of this fingerprint in 60s.

    Redis SET NX is the cross-replica gate. If Redis is the outage, fall
    back to a per-process timer so each replica still notifies once instead
    of once per ride.
    """
    key = f"{NOTIFY_PREFIX}{fingerprint}"
    try:
        return await redis_set_nx(key, "1", NOTIFY_DEDUP_SECONDS)
    except Exception:
        now = time.monotonic()
        until = _local_notify_until.get(fingerprint, 0.0)
        if now < until:
            return False
        _local_notify_until[fingerprint] = now + NOTIFY_DEDUP_SECONDS
        return True


async def notify_dispatch_geo_ops(event: dict) -> None:
    """Push a dispatch-geo event to the admin live map + Sentry.

    Deduped so a Redis outage does not emit one Sentry event per hail.
    The live-map banner still polls GET /dispatch-geo independently.
    """
    try:
        loud = await should_broadcast_ops(_notify_fingerprint(event))
    except Exception:
        loud = True
    if not loud:
        return
    try:
        try:
            from ..socket_manager import manager
        except ImportError:
            from socket_manager import manager  # type: ignore
        await manager.broadcast_to_admins({"type": "dispatch_geo_event", **event})
    except Exception as exc:
        logger.error("dispatch_geo admin WS broadcast failed: %s", exc, exc_info=True)
    if (event.get("kind") or "") not in _SENTRY_KINDS:
        return
    try:
        import sentry_sdk

        scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with scope_cm() as scope:
            scope.set_tag("domain", "dispatch")
            scope.set_tag("surface", "backend")
            if event.get("ride_id"):
                scope.set_tag("ride_id", str(event["ride_id"]))
            sentry_sdk.capture_message(
                f"dispatch geo {event.get('kind')} "
                f"{event.get('from_provider') or ''}→{event.get('to_provider') or ''}: "
                f"{event.get('reason')}",
                level="error",
            )
    except Exception:
        logger.debug("dispatch geo Sentry capture skipped", exc_info=True)


async def _load_last_served_slots() -> dict[str, dict[str, Any]]:
    try:
        raw = await redis_get(LAST_SERVED_KEY)
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                # Migrate the original single-payload shape.
                if "provider" in parsed and "failed_over" in parsed and "slots" not in parsed:
                    return {"_": parsed}
                if all(isinstance(v, dict) for v in parsed.values()):
                    return {str(k): v for k, v in parsed.items()}
    except Exception as exc:
        logger.error("h3 load last_served failed: %s", exc, exc_info=True)
    return dict(_local_last_served_slots)


async def remember_last_served(
    *,
    provider: str,
    configured: str,
    failed_over: bool,
    reason: str = "",
    ride_id: Optional[str] = None,
    area_id: Optional[str] = None,
) -> None:
    """What matching actually used on the last hail, keyed per service area."""
    payload: dict[str, Any] = {
        "provider": provider,
        "configured": configured,
        "failed_over": bool(failed_over),
        "reason": (reason or "")[:160],
        "ride_id": ride_id,
        "area_id": area_id,
        "at": _now_iso(),
    }
    slot = _area_slot(area_id)
    _local_last_served_slots[slot] = payload
    try:
        slots = await _load_last_served_slots()
        slots[slot] = payload
        await redis_set(LAST_SERVED_KEY, json.dumps(slots), ttl=LAST_SERVED_TTL_SECONDS)
    except Exception as exc:
        logger.error("h3 remember_last_served failed: %s", exc, exc_info=True)


def _pick_last_served(slots: dict[str, dict[str, Any]], area_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    if not slots:
        return None
    if area_id is not None:
        return slots.get(_area_slot(area_id)) or slots.get("_")
    failed = [v for v in slots.values() if isinstance(v, dict) and v.get("failed_over")]
    pool = failed or [v for v in slots.values() if isinstance(v, dict)]
    if not pool:
        return None
    pool.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return pool[0]


async def get_last_served(area_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    slots = await _load_last_served_slots()
    picked = _pick_last_served(slots, area_id)
    if picked:
        return picked
    return _pick_last_served(_local_last_served_slots, area_id)


def _rev_from_mapping(mapping: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not mapping:
        return None
    # HASH shape.
    if "7" in mapping or "8" in mapping or "ts" in mapping:
        cells = {}
        for res in _RESOLUTIONS:
            cell = mapping.get(str(res))
            if cell:
                cells[str(res)] = cell
        ts_raw = mapping.get("ts")
        try:
            ts_epoch = float(ts_raw) if ts_raw not in (None, "") else 0.0
        except (TypeError, ValueError):
            ts_epoch = 0.0
        return {"cells": cells, "ts_epoch": ts_epoch}
    # Legacy JSON {"cells": {...}, "ts": iso}.
    cells = mapping.get("cells") or {}
    if isinstance(cells, dict):
        ts_epoch = mapping.get("ts_epoch")
        if ts_epoch is None:
            ts_epoch = 0.0
        try:
            ts_epoch = float(ts_epoch)
        except (TypeError, ValueError):
            ts_epoch = 0.0
        return {"cells": {str(k): v for k, v in cells.items() if v}, "ts_epoch": ts_epoch}
    return None


async def _load_rev(driver_id: str) -> Optional[dict]:
    try:
        mapping = await redis_hgetall(_rev_key(driver_id))
        parsed = _rev_from_mapping(mapping)
        if parsed:
            return parsed
    except Exception:
        logger.debug("Redis reverse-index hash read failed", exc_info=True)
    raw = await redis_get(_rev_key(driver_id))
    if not raw:
        return None
    try:
        parsed_json = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return _rev_from_mapping(parsed_json) if isinstance(parsed_json, dict) else None


async def _upsert_python(driver_id: str, cells: dict[int, str], ts: float) -> bool:
    """In-process / Lua-fallback path. Same stale-ts + move semantics as Lua."""
    old = await _load_rev(driver_id)
    old_ts = float((old or {}).get("ts_epoch") or 0.0)
    if old_ts > ts:
        return False
    old_cells = (old or {}).get("cells") or {}
    for res, cell in cells.items():
        await redis_zadd(_cell_key(res, cell), {driver_id: ts}, ttl=INDEX_TTL_SECONDS)
        prev = old_cells.get(str(res)) or old_cells.get(res)
        if prev and prev != cell:
            await redis_zrem(_cell_key(res, prev), driver_id)
    await redis_hset(
        _rev_key(driver_id),
        {
            "ts": str(ts),
            "7": cells.get(7, ""),
            "8": cells.get(8, ""),
            "9": cells.get(9, ""),
        },
        ttl=INDEX_TTL_SECONDS,
    )
    return True


async def upsert_driver(
    driver_id: str,
    lat: float,
    lng: float,
    *,
    source_ts: Optional[float] = None,
) -> bool:
    """Move ``driver_id`` into the cells for ``lat/lng``. Returns False on error.

    ``source_ts`` is unix epoch of the GPS fix. A write with an older ts than
    the reverse record is skipped so the reconciler cannot clobber a newer
    heartbeat.
    """
    if not driver_id:
        return False
    try:
        cells = cells_for_all_resolutions(lat, lng)
    except ValueError:
        return False
    ts = float(source_ts) if source_ts is not None else time.time()
    try:
        try:
            await redis_eval(
                _UPSERT_LUA,
                1,
                _rev_key(driver_id),
                str(ts),
                str(driver_id),
                str(INDEX_TTL_SECONDS),
                CELL_PREFIX,
                cells.get(7, ""),
                cells.get(8, ""),
                cells.get(9, ""),
            )
        except RuntimeError:
            await _upsert_python(driver_id, cells, ts)
        except Exception as exc:
            logger.warning("h3 upsert lua failed, using python path: %s", exc)
            await _upsert_python(driver_id, cells, ts)
        return True
    except Exception as exc:
        logger.error("h3 upsert_driver failed driver_id=%s: %s", driver_id, exc, exc_info=True)
        _metric_inc("spinr_dispatch_h3_write_error_total", {"op": "upsert"})
        await mark_unhealthy("write_failed", extra={"op": "upsert"})
        return False


async def touch_driver(driver_id: str) -> None:
    """Refresh per-member zset scores without moving cells (WebSocket heartbeat)."""
    if not driver_id:
        return
    try:
        rec = await _load_rev(driver_id)
        if not rec:
            return
        ts = time.time()
        cells = rec.get("cells") or {}
        for res, cell in cells.items():
            if cell:
                await redis_zadd(_cell_key(int(res), cell), {driver_id: ts}, ttl=INDEX_TTL_SECONDS)
        await redis_hset(_rev_key(driver_id), {"ts": str(ts)}, ttl=INDEX_TTL_SECONDS)
    except Exception as exc:
        logger.error("h3 touch_driver failed driver_id=%s: %s", driver_id, exc, exc_info=True)
        _metric_inc("spinr_dispatch_h3_write_error_total", {"op": "touch"})
        await mark_unhealthy("write_failed", extra={"op": "touch"})


async def remove_driver(driver_id: str) -> None:
    """Drop a driver from every cell (go-offline, tombstone, expiry)."""
    if not driver_id:
        return
    try:
        rec = await _load_rev(driver_id)
        cells = (rec or {}).get("cells") or {}
        for res, cell in cells.items():
            if cell:
                await redis_zrem(_cell_key(int(res), cell), driver_id)
        await redis_delete(_rev_key(driver_id))
    except Exception as exc:
        logger.error("h3 remove_driver failed driver_id=%s: %s", driver_id, exc, exc_info=True)
        _metric_inc("spinr_dispatch_h3_write_error_total", {"op": "remove"})
        await mark_unhealthy("write_failed", extra={"op": "remove"})


async def query_driver_ids(
    lat: float,
    lng: float,
    radius_km: float,
    *,
    res: int = DEFAULT_DISPATCH_RESOLUTION,
) -> set[str]:
    """IDs in the k-ring covering the search circle. Raises on Redis errors.

    Drops to a coarser stored resolution when the disk would exceed
    ``MAX_H3_QUERY_CELLS``. Still too large → :class:`H3DiskTooLargeError`
    so matching failovers to PostGIS.
    """
    query_res = resolution_for_query(radius_km, res)
    cells = cells_covering(lat, lng, radius_km, query_res)
    keys = [_cell_key(query_res, c) for c in cells]
    min_score = time.time() - INDEX_TTL_SECONDS
    return await redis_zrangebyscore_many(keys, min_score)


async def set_ready(*, generation: int, driver_count: int, incomplete: bool = False) -> None:
    payload = {
        "generation": int(generation),
        "driver_count": int(driver_count),
        "incomplete": bool(incomplete),
        "rebuilt_at": _now_iso(),
    }
    await redis_set(READY_KEY, json.dumps(payload))
    _metric_gauge("spinr_dispatch_h3_index_drivers", float(driver_count))
    if incomplete:
        await mark_unhealthy("rebuild_incomplete", extra={"generation": generation})
    else:
        await clear_unhealthy()
        await record_event("ready", reason="rebuild_ok", extra={"generation": generation, "driver_count": driver_count})


async def _readiness_record() -> Optional[dict]:
    raw = await redis_get(READY_KEY)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def _unhealthy_record() -> Optional[dict]:
    raw = await redis_get(UNHEALTHY_KEY)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _policy_blocker(policy: Optional[str]) -> Optional[str]:
    if not policy or not str(policy).strip():
        return "eviction_policy:unknown"
    p = str(policy).strip().lower()
    if p != REQUIRED_EVICTION_POLICY:
        return f"eviction_policy:{p}"
    return None


def _eviction_blocks_h3(policy: Optional[str]) -> bool:
    return _policy_blocker(policy) is not None


def _memory_blocker(stats: dict[str, Any]) -> Optional[str]:
    maxmem = stats.get("maxmemory_bytes")
    pct = stats.get("used_memory_percent")
    try:
        maxmem_n = int(maxmem) if maxmem is not None else None
    except (TypeError, ValueError):
        maxmem_n = None
    if maxmem_n == 0:
        # Unlimited maxmemory + required noeviction is safe (nothing to evict).
        return None
    if pct is None:
        return "memory_percent_unknown"
    try:
        pct_f = float(pct)
    except (TypeError, ValueError):
        return "memory_percent_unknown"
    if pct_f >= MEMORY_HEADROOM_PERCENT:
        return f"memory_percent:{pct_f:.1f}"
    return None


async def cached_redis_stats() -> dict[str, Any]:
    """INFO is 4 round-trips; cache 15s so is_ready() is cheap on the hail path."""
    global _redis_stats_cache
    now = time.monotonic()
    if _redis_stats_cache is not None and now - _redis_stats_cache[0] < REDIS_STATS_CACHE_SECONDS:
        return _redis_stats_cache[1]
    stats = await get_redis_stats()
    _redis_stats_cache = (now, stats)
    return stats


def invalidate_redis_stats_cache() -> None:
    global _redis_stats_cache
    _redis_stats_cache = None


async def readiness_reasons(stats: Optional[dict] = None) -> tuple[bool, list[str]]:
    """Whether H3 may serve dispatch, plus human-readable blockers."""
    blockers: list[str] = []
    try:
        stats = stats if stats is not None else await cached_redis_stats()
    except Exception as exc:
        return False, [f"redis_stats_failed:{exc}"]

    backend = (stats or {}).get("backend")
    if backend != "redis" or not (stats or {}).get("connected"):
        blockers.append("redis_not_connected")
    else:
        policy_block = _policy_blocker((stats or {}).get("maxmemory_policy"))
        if policy_block:
            blockers.append(policy_block)
        mem_block = _memory_blocker(stats or {})
        if mem_block:
            blockers.append(mem_block)
    unhealthy = await _unhealthy_record()
    if unhealthy:
        blockers.append(f"unhealthy:{unhealthy.get('reason') or 'unknown'}")
    ready = await _readiness_record()
    if not ready:
        blockers.append("index_not_built")
    elif ready.get("incomplete"):
        blockers.append("rebuild_incomplete")
    return (len(blockers) == 0), blockers


async def is_ready() -> bool:
    ok, _ = await readiness_reasons()
    return ok


async def activation_blockers(provider: str) -> list[str]:
    """Settings/area writes that turn H3 on must not silently no-op at hail time."""
    p = (provider or "").strip().lower()
    if p not in {"h3", "shadow"}:
        return []
    _, blockers = await readiness_reasons()
    if p == "shadow":
        return [
            b for b in blockers if not (b in {"index_not_built", "rebuild_incomplete"} or b.startswith("unhealthy:"))
        ]
    return blockers


async def require_provider_activation(provider: Optional[str]) -> None:
    p = (provider or "").strip().lower()
    if not p or p in {"legacy", "postgis"}:
        return
    blockers = await activation_blockers(p)
    if blockers:
        raise ValueError(
            f"Cannot enable {p}: {', '.join(blockers)}. "
            "Redis must be connected, maxmemory-policy=noeviction, and memory "
            "under 60%. For H3, the index must also be complete — enable Shadow, "
            "Rebuild, then switch to H3."
        )


async def h3_index_writes_enabled() -> bool:
    """GPS/presence should not populate Redis while the provider is dark."""
    try:
        flag = await redis_get(WRITES_KEY)
        if flag == "1":
            return True
        if flag == "0":
            return False
    except Exception:
        logger.debug("Redis H3 write-provider flag read failed", exc_info=True)
    try:
        try:
            from ..settings_loader import get_cached_app_settings
        except ImportError:
            from settings_loader import get_cached_app_settings  # type: ignore
        cached = get_cached_app_settings() or {}
    except Exception:
        cached = {}
    p = str(cached.get("dispatch_geo_provider") or "legacy").lower()
    return p in {"h3", "shadow"}


async def refresh_h3_write_flag(app_settings: Optional[dict] = None) -> bool:
    """Set ``spinr:h3:writes_enabled`` from global settings + per-area overrides."""
    try:
        try:
            from .. import db_supabase as db
            from ..settings_loader import get_app_settings
        except ImportError:
            import db_supabase as db  # type: ignore
            from settings_loader import get_app_settings  # type: ignore
        settings = app_settings if app_settings is not None else await get_app_settings()
        wanted = str((settings or {}).get("dispatch_geo_provider") or "legacy").lower() in {"h3", "shadow"}
        if not wanted:
            rows = await db.get_rows(
                "service_areas",
                {"dispatch_geo_provider": {"$in": ["h3", "shadow"]}},
                columns="id",
                limit=1,
            )
            wanted = bool(rows)
    except Exception as exc:
        logger.error("h3 refresh_h3_write_flag failed: %s", exc, exc_info=True)
        wanted = False
    try:
        await redis_set(WRITES_KEY, "1" if wanted else "0")
    except Exception as exc:
        logger.error("h3 write-flag redis_set failed: %s", exc, exc_info=True)
    return wanted


async def health_snapshot() -> dict[str, Any]:
    """Admin-dashboard payload: current index health, no PII."""
    stats: dict[str, Any] = {}
    try:
        stats = await cached_redis_stats()
    except Exception as exc:
        stats = {"connected": False, "error": str(exc)}
    ok, blockers = await readiness_reasons(stats)
    ready = await _readiness_record()
    unhealthy = await _unhealthy_record()
    events = await recent_events(20)
    evicted = stats.get("evicted_keys_total")
    last_served = await get_last_served()
    return {
        "h3_ready": ok,
        "blockers": blockers,
        "index": ready,
        "unhealthy": unhealthy,
        "last_served": last_served,
        "events": events,
        "redis": {
            "backend": stats.get("backend"),
            "connected": stats.get("connected"),
            "maxmemory_policy": stats.get("maxmemory_policy"),
            "used_memory_percent": stats.get("used_memory_percent"),
            "used_memory_human": stats.get("used_memory_human"),
            "evicted_keys_total": evicted,
            "error": stats.get("error"),
        },
        "index_ttl_seconds": INDEX_TTL_SECONDS,
        "memory_headroom_percent": MEMORY_HEADROOM_PERCENT,
        "required_eviction_policy": REQUIRED_EVICTION_POLICY,
    }


async def on_location_written(
    driver_id: str,
    lat: Any,
    lng: Any,
    *,
    source_ts: Optional[float] = None,
    force: bool = False,
) -> bool:
    """GPS-path hook: never raises. No-op while the provider is dark.

    Rebuilds pass ``force=True`` so a dark index can still be healed.
    Returns whether the driver was written into the index.
    """
    try:
        if not force and not await h3_index_writes_enabled():
            return False
        return await upsert_driver(driver_id, float(lat), float(lng), source_ts=source_ts)
    except Exception as exc:  # pragma: no cover - upsert already swallows
        logger.error("h3 on_location_written failed: %s", exc, exc_info=True)
        return False


async def on_driver_heartbeat(driver_id: str) -> None:
    try:
        if not await h3_index_writes_enabled():
            return
        await touch_driver(driver_id)
    except Exception as exc:  # pragma: no cover
        logger.error("h3 on_driver_heartbeat failed: %s", exc, exc_info=True)


async def on_driver_offline(driver_id: str) -> None:
    """Always drop the driver — go-offline must not leave a stale index hit."""
    try:
        await remove_driver(driver_id)
    except Exception as exc:  # pragma: no cover
        logger.error("h3 on_driver_offline failed: %s", exc, exc_info=True)
