"""Rebuild the Redis H3 live-location index from Postgres.

Interval: 2 minutes. Replay-safety: Redis leader lock (load shedding) plus
a generation stamp — a crashed rebuild leaves ``incomplete=true`` on the
ready record so dispatch treats H3 as unready and falls back.

Reads online+available drivers with a lat/lng, upserts each into the
index, then marks the generation ready. Does not delete cell keys first
(live GPS writes race with the rebuild); stale IDs are dropped by the
Postgres eligibility re-read on the matching path.

Must not run as the dispatch source of truth: this loop exists so a
cold Redis, a missed offline, or a write failure can heal without a
deploy. A crash mid-tick leaves the previous ready record in place so
live H3 dispatch is not forced onto PostGIS every two minutes.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase as db
    from .h3_location_index import (
        h3_index_writes_enabled,
        on_location_written,
        record_event,
        refresh_h3_write_flag,
        set_ready,
    )
    from .redis_client import redis_get, redis_set, try_acquire_leader_lock
except ImportError:
    import db_supabase as db  # type: ignore
    from utils.h3_location_index import (  # type: ignore
        h3_index_writes_enabled,
        on_location_written,
        record_event,
        refresh_h3_write_flag,
        set_ready,
    )
    from utils.redis_client import redis_get, redis_set, try_acquire_leader_lock  # type: ignore

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 120
GEN_KEY = "spinr:h3:generation"
PAGE_SIZE = 500
LOOP_NAME = "h3_index_reconciler (2min)"


def _source_ts(value: Any) -> Optional[float]:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


async def h3_index_reconciler_loop() -> None:
    """Rebuild H3 membership from Postgres. 2 min. Leader-locked."""
    await asyncio.sleep(random.uniform(5, 20))
    while True:
        try:
            await refresh_h3_write_flag()
            if await h3_index_writes_enabled():
                await _tick()
        except Exception:
            logger.error("h3_index_reconciler tick failed", exc_info=True)
        _record_heartbeat(LOOP_NAME)
        await asyncio.sleep(INTERVAL_SECONDS)


async def _next_generation() -> int:
    raw = await redis_get(GEN_KEY)
    try:
        current = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        current = 0
    nxt = current + 1
    await redis_set(GEN_KEY, str(nxt))
    return nxt


async def _tick(*, force: bool = False) -> dict[str, Any]:
    if not force and not await try_acquire_leader_lock(
        "h3_index_reconciler", int(INTERVAL_SECONDS * 0.85), logger_=logger
    ):
        return {"ok": False, "skipped": True, "reason": "leader_lock_held"}
    # Do not flip the ready key to incomplete at the start of a tick: that
    # would force PostGIS failover every 2 minutes while H3 is the live
    # provider. A crash mid-upsert leaves the previous ready record in place;
    # live GPS writes keep cells current. Only a never-built index stays unready.
    offset = 0
    upserted = 0
    failed = 0
    while True:
        rows = await db.get_rows(
            "drivers",
            {
                "is_online": True,
                "is_available": True,
                "deleted_at": None,
            },
            columns="id,lat,lng,updated_at",
            limit=PAGE_SIZE,
            offset=offset,
        )
        if not rows:
            break
        for row in rows:
            lat, lng = row.get("lat"), row.get("lng")
            driver_id = row.get("id")
            if driver_id is None or lat is None or lng is None:
                continue
            if (lat == 0 and lng == 0) or (lat == 0.0 and lng == 0.0):
                continue
            ok = await on_location_written(
                str(driver_id),
                lat,
                lng,
                source_ts=_source_ts(row.get("updated_at")),
                force=True,
            )
            if ok:
                upserted += 1
            else:
                failed += 1
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    generation = await _next_generation()
    incomplete = failed > 0
    await set_ready(generation=generation, driver_count=upserted, incomplete=incomplete)
    logger.info(
        "h3_index_reconciler generation=%s drivers=%s failed=%s incomplete=%s at=%s",
        generation,
        upserted,
        failed,
        incomplete,
        datetime.now(timezone.utc).isoformat(),
    )
    await record_event(
        "rebuild",
        reason="incomplete" if incomplete else "ok",
        extra={"generation": generation, "driver_count": upserted, "failed": failed},
    )
    return {
        "ok": not incomplete,
        "skipped": False,
        "incomplete": incomplete,
        "generation": generation,
        "driver_count": upserted,
        "failed": failed,
    }
