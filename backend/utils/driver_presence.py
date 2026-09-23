"""
Driver presence — online by recent client activity.

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
``PRESENCE_TTL`` is a renewable 90-second window from the latest activity,
not a limit on time spent in the background. Background GPS requests updates
every 10 seconds, but native scheduling and cellular handoffs can delay them.
Discovery, estimates and dispatch use the same window. Explicit offline and
revocation paths clear it immediately; total silence expires within 90 seconds.

Redis transparency
------------------
All helpers go through ``utils.redis_client`` which falls back to an
in-process dict when ``REDIS_URL`` is unset. Single-replica dev works
without Redis; multi-replica prod must have Redis configured for
presence to fan out correctly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional
from urllib.parse import quote

try:
    from .redis_client import (
        _get_redis,
        _local,
        redis_delete,
        redis_expire,
        redis_get,
        redis_incr,
        redis_set,
    )
except ImportError:  # pragma: no cover
    from utils.redis_client import (  # type: ignore
        _get_redis,
        _local,
        redis_delete,
        redis_expire,
        redis_get,
        redis_incr,
        redis_set,
    )

logger = logging.getLogger(__name__)

# Allow background delivery jitter within the dispatch domain's 90s upper bound.
PRESENCE_TTL = 90

_PREFIX = "spinr:presence:driver:"
_SCOPED_PREFIX = "spinr:presence:v2:driver:"

_MERGE_SCOPED_PRESENCE = """
local contact_ms = tonumber(ARGV[1])
local contact_deadline_ms = tonumber(ARGV[2])
local location_deadline_ms = tonumber(ARGV[3])
local old_contact_ms = tonumber(redis.call('HGET', KEYS[1], 'contact_received_ms') or '0')
local old_contact_deadline_ms = tonumber(redis.call('HGET', KEYS[1], 'contact_valid_until_ms') or '0')
local old_location_deadline_ms = tonumber(redis.call('HGET', KEYS[1], 'location_valid_until_ms') or '0')
if contact_ms >= old_contact_ms then
    redis.call('HSET', KEYS[1], 'contact_received_ms', ARGV[1], 'contact_valid_until_ms', ARGV[2])
end
if location_deadline_ms > old_location_deadline_ms then
    redis.call('HSET', KEYS[1], 'location_valid_until_ms', ARGV[3])
end
local latest_contact_deadline_ms = math.max(contact_deadline_ms, old_contact_deadline_ms)
redis.call('PEXPIREAT', KEYS[1], latest_contact_deadline_ms)
return redis.call('HGETALL', KEYS[1])
"""


def _key(driver_id: str) -> str:
    return f"{_PREFIX}{driver_id}"


def scoped_presence_key(driver_id: str, session_id: str, online_epoch: int) -> str:
    """Return an isolated v2 key; legacy writes cannot replace scoped evidence."""
    if not driver_id or not session_id or type(online_epoch) is not int or online_epoch < 0:
        raise ValueError("driver, session, and non-negative online epoch are required")
    return f"{_SCOPED_PREFIX}{quote(driver_id, safe='')}:session:{quote(session_id, safe='')}:epoch:{online_epoch}"


def _timestamp_milliseconds(value: str | datetime | None) -> int:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _milliseconds_datetime(value: Any) -> datetime | None:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc) if milliseconds > 0 else None


def bind_ws_presence_epoch(
    conn_state: dict, session_id: str | None, requested_epoch: str | None, snapshot: dict
) -> bool:
    """Bind only the decimal epoch explicitly presented by this socket."""
    conn_state.pop("presence_epoch", None)
    conn_state.pop("presence_session_id", None)
    if (
        not session_id
        or not isinstance(requested_epoch, str)
        or not requested_epoch.isascii()
        or not requested_epoch.isdecimal()
        or len(requested_epoch) > 19
        or int(requested_epoch) > 9_223_372_036_854_775_807
        or not snapshot.get("is_online")
        or str(snapshot.get("online_epoch")) != requested_epoch
    ):
        conn_state["presence_reconcile_required"] = True
        return False
    conn_state["presence_session_id"] = session_id
    conn_state["presence_epoch"] = requested_epoch
    conn_state.pop("presence_reconcile_required", None)
    return True


def ws_session_was_superseded(result: dict[str, Any]) -> bool:
    """Whether durable renewal proved this JWT controller has been replaced."""
    return result.get("code") == "SESSION_SUPERSEDED"


def scoped_marker_allows_fanout(accepted: Any, *, fenced: bool, attempted: bool = True) -> bool:
    """Require an actual DB acceptance before v2 marker delivery/fanout."""
    if fenced:
        return attempted and accepted is True
    return accepted is not False


async def renew_ws_presence(
    driver_id: str, conn_state: dict, *, location_captured_at: datetime | None = None
) -> dict[str, Any] | None:
    """Renew this immutable socket fence; omitted GPS means contact-only."""
    epoch = conn_state.get("presence_epoch")
    if epoch is None:
        return None
    kwargs = {"location_captured_at": location_captured_at} if location_captured_at is not None else {}
    result = await renew_driver_presence(
        driver_id,
        conn_state["presence_session_id"],
        int(epoch),
        **kwargs,
    )
    if result.get("status") in {"stale_epoch", "offline"}:
        conn_state["presence_epoch"] = None
        conn_state["presence_reconcile_required"] = True
    return result


async def renew_ws_batch_location(
    driver_id: str, conn_state: dict, captured_at: datetime, *, trusted: bool
) -> dict[str, Any] | None:
    """Renew approved batch GPS only through the batch socket's immutable fence."""
    if not trusted or conn_state.get("presence_epoch") is None:
        return None
    return await renew_ws_presence(driver_id, conn_state, location_captured_at=captured_at)


async def clear_ws_presence(driver_id: str, conn_state: dict) -> None:
    """Clear only the exact scoped socket key; legacy clear is dark-path only."""
    epoch = conn_state.get("presence_epoch")
    if epoch is not None:
        await clear_scoped_driver_presence(driver_id, conn_state["presence_session_id"], int(epoch))
    elif not conn_state.get("availability_v2"):
        await clear_presence(driver_id)


async def _merge_scoped_presence(key: str, result: dict[str, Any]) -> dict[str, Any]:
    """Atomically preserve newest contact and approved-GPS deadlines per fence."""
    received_ms = _timestamp_milliseconds(result.get("contact_received_at"))
    contact_until_ms = _timestamp_milliseconds(result.get("contact_valid_until"))
    location_until_ms = _timestamp_milliseconds(result.get("location_valid_until"))
    redis_client = await _get_redis()
    if redis_client is None:
        raise RuntimeError("Scoped presence requires shared Redis")
    values = await redis_client.eval(
        _MERGE_SCOPED_PRESENCE,
        1,
        key,
        received_ms,
        contact_until_ms,
        location_until_ms,
    )
    if isinstance(values, list):
        fields = {}
        for name, value in zip(values[::2], values[1::2], strict=False):
            name = name.decode() if isinstance(name, bytes) else str(name)
            value = value.decode() if isinstance(value, bytes) else str(value)
            fields[name] = value
        return fields
    return await redis_client.hgetall(key)


async def renew_driver_presence(
    driver_id: str,
    session_id: str,
    online_epoch: int,
    *,
    location_captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Renew server-authenticated contact and optional integrity-approved GPS."""
    try:
        try:
            from ..repositories.driver_presence_repo import renew_driver_presence as renew_in_database
        except ImportError:  # pragma: no cover - top-level backend import mode
            from repositories.driver_presence_repo import renew_driver_presence as renew_in_database  # type: ignore

        result = await renew_in_database(driver_id, session_id, online_epoch, location_captured_at)
    except Exception:
        logger.error("[presence] durable renewal failed", exc_info=True)
        return {"status": "unavailable", "code": "PRESENCE_UNAVAILABLE", "online_epoch": str(online_epoch)}

    status = result.get("status")
    code = result.get("code")
    if status != "renewed":
        external_code = {
            "UNAUTHORIZED_SESSION": "SESSION_SUPERSEDED",
            "CONTROLLER_SESSION_MISMATCH": "SESSION_SUPERSEDED",
            "ONLINE_EPOCH_STALE": "ONLINE_EPOCH_STALE",
            "OFFLINE": "DRIVER_OFFLINE",
            "CONTACT_GAP": "CONTACT_GAP",
            "AVAILABILITY_V2_DISABLED": "AVAILABILITY_V2_DISABLED",
        }.get(code, code or "PRESENCE_UNAVAILABLE")
        return {**result, "code": external_code}

    try:
        key = scoped_presence_key(driver_id, session_id, online_epoch)
        fields = await _merge_scoped_presence(key, result)
    except Exception:
        logger.error("[presence] scoped Redis renewal failed", exc_info=True)
        return {"status": "unavailable", "code": "PRESENCE_UNAVAILABLE", "online_epoch": str(online_epoch)}

    return {
        **result,
        "code": "INVALID_LOCATION_TIME" if code == "INVALID_LOCATION_TIME" else "renewed",
        "contact_valid_until": _milliseconds_datetime(fields.get("contact_valid_until_ms")),
        "location_valid_until": _milliseconds_datetime(fields.get("location_valid_until_ms")),
    }


async def get_scoped_driver_presence(driver_id: str, session_id: str, online_epoch: int) -> dict[str, Any] | None:
    """Read only the matching controller and epoch's expiring evidence."""
    key = scoped_presence_key(driver_id, session_id, online_epoch)
    redis_client = await _get_redis()
    if redis_client is None:
        raise RuntimeError("Scoped presence requires shared Redis")
    fields = await redis_client.hgetall(key)
    fields = {
        (name.decode() if isinstance(name, bytes) else str(name)): (
            value.decode() if isinstance(value, bytes) else str(value)
        )
        for name, value in (fields or {}).items()
    }
    if not fields:
        return None
    return {
        "contact_received_at": _milliseconds_datetime(fields.get("contact_received_ms")),
        "contact_valid_until": _milliseconds_datetime(fields.get("contact_valid_until_ms")),
        "location_valid_until": _milliseconds_datetime(fields.get("location_valid_until_ms")),
    }


async def clear_scoped_driver_presence(driver_id: str, session_id: str, online_epoch: int) -> None:
    """Delete only the exact controller lease; old disconnects cannot clear newer keys."""
    redis_client = await _get_redis()
    if redis_client is None:
        raise RuntimeError("Scoped presence requires shared Redis")
    await redis_client.delete(scoped_presence_key(driver_id, session_id, online_epoch))


async def mark_present(driver_id: str, ttl: int = PRESENCE_TTL) -> None:
    """Refresh this driver's presence key. Idempotent; safe to call often."""
    if not driver_id:
        return
    try:
        await redis_set(_key(driver_id), "1", ttl=ttl)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"[presence] mark_present({driver_id}) failed: {exc}", exc_info=True)


async def is_present(driver_id: str) -> bool:
    """True iff a non-expired presence key exists for this driver."""
    if not driver_id:
        return False
    try:
        val = await redis_get(_key(driver_id))
        return val is not None
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"[presence] is_present({driver_id}) failed: {exc}", exc_info=True)
        return False


async def clear_presence(driver_id: str) -> None:
    """Delete this driver's presence key — call on explicit sign-out / go-offline.

    Also purges the driver from the H3 dispatch index (``on_driver_offline``)
    — presence and the geo index must agree on who's reachable, and a driver
    leaving presence must not leave a stale index hit behind. This is the
    single choke point every explicit sign-out/go-offline/force-offline path
    already calls, so wiring it here covers all of them. Harmless no-op
    while the H3 feature is dark (writes never populated a record for this
    driver in the first place); safe to call even for a driver never
    written to the index.
    """
    if not driver_id:
        return
    try:
        await redis_delete(_key(driver_id))
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"[presence] clear_presence({driver_id}) failed: {exc}", exc_info=True)
    try:
        from .h3_location_index import on_driver_offline
    except ImportError:  # pragma: no cover
        from utils.h3_location_index import on_driver_offline  # type: ignore
    await on_driver_offline(driver_id)


async def present_driver_ids_checked(candidate_ids: List[str]) -> tuple[set, bool]:
    """Return ``(present_ids, reachable)`` for ``candidate_ids``.

    ``reachable`` lets a caller distinguish "the presence store says nobody
    is present" from "the presence store could not be consulted":

      * ``reachable=True``  — the set is authoritative. Either Redis answered,
        or Redis is unconfigured and the in-process dict (dev / single
        replica) is the source of truth. An empty set here genuinely means
        every candidate is offline.
      * ``reachable=False`` — Redis is *configured but unavailable* (failover,
        network blip). The set is unreliable and MUST NOT be treated as
        "everyone offline"; callers should fall back to durable DB state.

    Without this distinction the old ``present_driver_ids`` returned an empty
    set for both cases, so a Redis outage looked identical to a genuinely
    empty pool — blanking the rider map during failover.

    Batched (single MGET) so dispatch and admin monitoring filter a driver
    pool without N sequential round-trips.
    """
    if not candidate_ids:
        return set(), True

    r = await _get_redis()
    if r is not None:
        try:
            keys = [_key(i) for i in candidate_ids]
            vals = await r.mget(*keys)
            return {cid for cid, v in zip(candidate_ids, vals, strict=False) if v is not None}, True
        except Exception as exc:
            # Configured-but-down: the result is unknowable, not "all offline".
            logger.error(
                f"[presence] MGET failed — Redis configured but unavailable; presence is unreliable: {exc}",
                exc_info=True,
            )
            return set(), False

    # Redis unconfigured — in-process dict is authoritative (single replica, dev/test).
    result = set()
    for cid in candidate_ids:
        if await is_present(cid):
            result.add(cid)
    return result, True


async def scoped_driver_presence_evidence(candidate_ids: List[str]) -> tuple[dict[str, dict], bool]:
    """Return fresh scoped lease evidence by driver using durable scope + pipeline."""
    if not candidate_ids:
        return {}, True
    try:
        try:
            from ..repositories._base import get_rows
        except ImportError:  # pragma: no cover
            from repositories._base import get_rows  # type: ignore
        rows = await get_rows(
            "drivers",
            {"id": {"$in": candidate_ids}},
            limit=len(candidate_ids),
            columns="id,controller_session_id,online_epoch,is_online",
        )
    except Exception as exc:
        logger.error("scoped presence durable controller lookup failed: %s", exc, exc_info=True)
        return {}, False
    scoped_rows = [
        row for row in rows
        if row.get("is_online") is True
        and isinstance(row.get("controller_session_id"), str)
        and type(row.get("online_epoch")) is int
        and row["online_epoch"] >= 0
    ]
    if not scoped_rows:
        return {}, True
    redis_client = await _get_redis()
    if redis_client is None:
        return {}, False
    try:
        pipe = redis_client.pipeline(transaction=False)
        for row in scoped_rows:
            pipe.hgetall(scoped_presence_key(row["id"], row["controller_session_id"], row["online_epoch"]))
        values = await pipe.execute()
    except Exception as exc:
        logger.error("scoped presence Redis pipeline failed: %s", exc, exc_info=True)
        return {}, False

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    result = {}
    for row, raw_fields in zip(scoped_rows, values, strict=False):
        fields = {
            (key.decode() if isinstance(key, bytes) else str(key)): (value.decode() if isinstance(value, bytes) else str(value))
            for key, value in (raw_fields or {}).items()
        }
        try:
            contact_until = int(fields.get("contact_valid_until_ms", "0"))
            location_until = int(fields.get("location_valid_until_ms", "0"))
        except (TypeError, ValueError):
            continue
        if contact_until > now_ms and location_until > now_ms:
            result[row["id"]] = {
                "session_id": row["controller_session_id"],
                "online_epoch": row["online_epoch"],
                "contact_valid_until_ms": contact_until,
                "location_valid_until_ms": location_until,
            }
    return result, True


async def scoped_present_driver_ids_checked(candidate_ids: List[str]) -> tuple[set, bool]:
    """Return only authoritative v2-scoped presence; never merge legacy keys."""
    evidence, reachable = await scoped_driver_presence_evidence(candidate_ids)
    return set(evidence), reachable


async def present_driver_ids(candidate_ids: List[str]) -> set:
    """Return the subset of ``candidate_ids`` that are currently present.

    Thin wrapper over :func:`present_driver_ids_checked` that drops the
    reachability flag. Callers that must survive a Redis outage without
    over-filtering should use the ``_checked`` variant directly.
    """
    present, _reachable = await present_driver_ids_checked(candidate_ids)
    return present


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
        logger.error(f"[presence] SCAN failed: {exc}", exc_info=True)
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


# ── Missed-offer streak tracking ─────────────────────────────────────────────
# Tracks consecutive offer timeouts per driver. When the streak reaches the
# configured threshold the driver is auto-offlined so they stop absorbing
# offers that expire and delay riders.

_MISS_PREFIX = "spinr:offer_miss_streak:"
_MISS_TTL = 1800  # 30 min — streak resets if no offers for this long


def _miss_key(driver_id: str) -> str:
    return f"{_MISS_PREFIX}{driver_id}"


async def increment_miss_streak(driver_id: str) -> int:
    """Bump the consecutive-miss counter and return the new value."""
    if not driver_id:
        return 0
    try:
        count = await redis_incr(_miss_key(driver_id))
        await redis_expire(_miss_key(driver_id), _MISS_TTL)
        return count
    except Exception as exc:
        logger.error(f"[presence] increment_miss_streak({driver_id}) failed: {exc}", exc_info=True)
        return 0


async def reset_miss_streak(driver_id: str) -> None:
    """Clear the streak — called when the driver accepts, declines, or goes online."""
    if not driver_id:
        return
    try:
        await redis_delete(_miss_key(driver_id))
    except Exception as exc:
        logger.error(f"[presence] reset_miss_streak({driver_id}) failed: {exc}", exc_info=True)


async def get_miss_streak(driver_id: str) -> int:
    """Return the current consecutive-miss count (0 if no key)."""
    if not driver_id:
        return 0
    try:
        val = await redis_get(_miss_key(driver_id))
        return int(val) if val else 0
    except Exception as exc:
        logger.error(f"[presence] get_miss_streak({driver_id}) failed: {exc}", exc_info=True)
        return 0
