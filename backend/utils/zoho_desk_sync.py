"""
Zoho Desk -> Postgres mirror sync.

Pulls recent tickets from Zoho Desk and upserts them into the
``zoho_desk_tickets`` table (migration 123) so the admin Help Desk can serve
lists, dashboards, and trends from our own DB instead of spending Zoho API
credits on every page view. Writes (reply/assign/status/create) still go live
to Zoho; the next sync reflects them.

Replay-safe: upserts keyed on ``zoho_id``, so every replica can run the loop
concurrently without creating duplicates. No-op when the integration is
disabled or not connected.

v1 strategy: each cycle re-pulls the most-recent N pages by createdTime and
upserts them. This captures new tickets and status changes on recent tickets
cheaply. (Modifications to very old tickets would need modifiedTime paging or
webhooks — a later enhancement.)
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from .. import db_supabase
    from ..services import zoho_desk_service as zoho
    from ..services.zoho_desk_integration import close_linked_records
    from ..services.zoho_desk_service import ZohoDeskError
    from ..utils.redis_client import redis_set_nx
except ImportError:  # pragma: no cover - allow direct module imports
    import db_supabase
    from services import zoho_desk_service as zoho
    from services.zoho_desk_integration import close_linked_records
    from services.zoho_desk_service import ZohoDeskError
    from utils.redis_client import redis_set_nx

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:  # pragma: no cover
    try:
        from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    except ImportError:  # pragma: no cover

        def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
            pass


logger = logging.getLogger(__name__)

_TABLE = "zoho_desk_tickets"
_CONFIG_TABLE = "zoho_desk_config"
_CONFIG_ID = "default"

SYNC_INTERVAL_SECONDS = 600  # 10 minutes
_LOOP_NAME = "zoho_desk_sync (10min)"
SEED_MAX_PAGES = 500  # first run: full backfill (safety cap ~50k tickets)
INCREMENTAL_MAX_PAGES = 50  # safety cap; incremental runs stop at the cursor

# Single-replica election: the loop runs on every replica, but only the lock
# winner calls Zoho per interval — without this, N replicas would each spend
# Zoho API credits every cycle. TTL is just under the interval so the lock
# frees before the next tick (letting the leader re-acquire, or another replica
# take over if the leader died). Fails open if Redis is down (degrades to the
# pre-existing per-replica behaviour, never blocks the sync).
_SYNC_LOCK_KEY = "spinr:zoho:sync:leader"
_SYNC_LOCK_TTL = SYNC_INTERVAL_SECONDS - 60
_INSTANCE_ID = uuid.uuid4().hex


def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _name(obj: Dict[str, Any]) -> str:
    full = f"{obj.get('firstName', '')} {obj.get('lastName', '')}".strip()
    return full or ""


def _map_ticket(t: Dict[str, Any]) -> Dict[str, Any]:
    contact = t.get("contact") or {}
    assignee = t.get("assignee") or {}
    tags = [tag.get("name") if isinstance(tag, dict) else tag for tag in (t.get("tags") or [])]
    return {
        "zoho_id": str(t.get("id") or ""),
        "ticket_number": str(t.get("ticketNumber") or ""),
        "subject": t.get("subject"),
        "status": t.get("status"),
        "status_type": t.get("statusType"),
        "priority": t.get("priority"),
        "channel": t.get("channel"),
        "category": t.get("category"),
        "classification": t.get("classification"),
        "department_id": t.get("departmentId"),
        "assignee_id": assignee.get("id") or t.get("assigneeId"),
        "assignee_name": _name(assignee) or None,
        "contact_email": (contact.get("email") or t.get("email") or None),
        "contact_name": _name(contact) or None,
        "tags": [n for n in tags if n],
        "created_time": t.get("createdTime"),
        "modified_time": t.get("modifiedTime"),
        "closed_time": t.get("closedTime"),
        "due_date": t.get("dueDate"),
        "web_url": t.get("webUrl"),
        "raw": t,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


async def _upsert_batch(rows: List[Dict[str, Any]]) -> None:
    if not rows or not db_supabase.supabase:
        return
    serialized = [db_supabase._serialize_for_api(r) for r in rows]

    def _fn():
        return db_supabase.supabase.table(_TABLE).upsert(serialized).execute()

    await db_supabase.run_sync(_fn)


async def run_sync() -> Dict[str, Any]:
    """Incrementally pull tickets by -modifiedTime and upsert into the mirror.

    Until the mirror is marked backfilled it does a FULL seed (pages to the end,
    ignoring any stale cursor). Once backfilled, runs are incremental and stop at
    the stored cursor — typically a single page. Skips when disabled.
    """
    cfg = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID})
    if not cfg or not cfg.get("enabled"):
        return {"skipped": "disabled"}

    # A mirror that hasn't been marked backfilled needs a FULL backfill,
    # regardless of any stale sync_cursor left by a pre-backfill sync (or an
    # environment upgraded before mirror_backfilled existed). Ignore the cursor
    # while seeding so we page all the way to the end instead of stopping at it.
    backfilled = bool(cfg.get("mirror_backfilled"))
    seeding = not backfilled
    cursor = None if seeding else _parse(cfg.get("sync_cursor"))
    newest = cursor
    max_pages = SEED_MAX_PAGES if seeding else INCREMENTAL_MAX_PAGES
    total = 0
    reached_end = False
    closed_ids: List[str] = []

    for p in range(max_pages):
        page = await zoho.list_tickets(from_index=p * 100 + 1, limit=100, sort_by="-modifiedTime")
        rows = (page or {}).get("data", [])
        if not rows:
            reached_end = True
            break

        batch: List[Dict[str, Any]] = []
        stop = False
        for t in rows:
            mod = _parse(t.get("modifiedTime"))
            if cursor and mod and mod <= cursor:
                stop = True
                break
            batch.append(_map_ticket(t))
            if mod and (newest is None or mod > newest):
                newest = mod
            if (t.get("statusType") or "").lower() == "closed" or "closed" in (t.get("status") or "").lower():
                closed_ids.append(str(t.get("id")))

        if batch:
            await _upsert_batch(batch)
            total += len(batch)
        if stop or len(rows) < 100:
            reached_end = True
            break

    # Reverse sync: close linked Spinr records (L&F / disputes / complaints /
    # safety / flags) for tickets that just closed. Skipped during the seed —
    # we only act on incremental transitions, not the initial backfill.
    if not seeding and closed_ids:
        await close_linked_records(closed_ids)

    updates: Dict[str, Any] = {
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
        "last_sync_count": total,
    }
    if newest:
        updates["sync_cursor"] = newest.isoformat()
    # The mirror becomes authoritative for reads only once a run has paged all
    # the way to the end (seed completed, or an incremental run reached the end).
    if reached_end:
        updates["mirror_backfilled"] = True
    await db_supabase.update_one(_CONFIG_TABLE, {"id": _CONFIG_ID}, updates)
    logger.info(
        "Zoho Desk sync upserted %s tickets (cursor=%s, backfilled=%s)",
        total,
        newest,
        updates.get("mirror_backfilled", cfg.get("mirror_backfilled")),
    )
    return {"upserted": total, "backfilled": bool(updates.get("mirror_backfilled") or cfg.get("mirror_backfilled"))}


async def zoho_desk_sync_loop() -> None:
    """Background loop: sync every SYNC_INTERVAL_SECONDS. Replay-safe (upsert).

    Gated on zoho_desk_config.auto_sync_enabled (default false). Manual "Sync
    now" (run_sync via the admin endpoint) is unaffected — only this periodic
    pull is opt-in.

    Runs on every replica, but a Redis leader lock ensures only ONE replica
    actually pulls from Zoho per interval, so API-credit usage doesn't scale
    with replica count. The pull itself is incremental (sorted by -modifiedTime,
    stops at the stored sync_cursor), so a quiet interval costs ~1 Zoho call.
    """
    while True:
        try:
            cfg = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID})
            if cfg and cfg.get("enabled") and cfg.get("auto_sync_enabled"):
                if await redis_set_nx(_SYNC_LOCK_KEY, _INSTANCE_ID, _SYNC_LOCK_TTL):
                    await run_sync()
                else:
                    logger.debug("Zoho Desk sync skipped — another replica holds the leader lock")
        except ZohoDeskError as e:
            # Integration not configured / scope issue — warn, keep looping.
            logger.warning("Zoho Desk sync skipped: %s", e.message)
        except Exception:
            logger.error("zoho_desk_sync tick failed", exc_info=True)
        _record_heartbeat(_LOOP_NAME)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
