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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from .. import db_supabase
    from ..services import zoho_desk_service as zoho
    from ..services.zoho_desk_service import ZohoDeskError
except ImportError:  # pragma: no cover - allow direct module imports
    import db_supabase
    from services import zoho_desk_service as zoho
    from services.zoho_desk_service import ZohoDeskError

logger = logging.getLogger(__name__)

_TABLE = "zoho_desk_tickets"
_CONFIG_TABLE = "zoho_desk_config"
_CONFIG_ID = "default"

SYNC_INTERVAL_SECONDS = 600  # 10 minutes
SEED_MAX_PAGES = 10  # first run: pull up to 1000 most-recently-modified tickets
INCREMENTAL_MAX_PAGES = 50  # safety cap; incremental runs stop at the cursor


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

    Stops as soon as it reaches a ticket modified at/before the stored cursor,
    so a steady-state run is typically a single page. The first run (no cursor)
    seeds up to SEED_MAX_PAGES. Skips silently when the integration is disabled.
    """
    cfg = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID})
    if not cfg or not cfg.get("enabled"):
        return {"skipped": "disabled"}

    cursor = _parse(cfg.get("sync_cursor"))
    newest = cursor
    max_pages = INCREMENTAL_MAX_PAGES if cursor else SEED_MAX_PAGES
    total = 0

    for p in range(max_pages):
        page = await zoho.list_tickets(from_index=p * 100 + 1, limit=100, sort_by="-modifiedTime")
        rows = (page or {}).get("data", [])
        if not rows:
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

        if batch:
            await _upsert_batch(batch)
            total += len(batch)
        if stop or len(rows) < 100:
            break

    updates: Dict[str, Any] = {
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
        "last_sync_count": total,
    }
    if newest:
        updates["sync_cursor"] = newest.isoformat()
    await db_supabase.update_one(_CONFIG_TABLE, {"id": _CONFIG_ID}, updates)
    logger.info("Zoho Desk sync upserted %s tickets (cursor=%s)", total, newest)
    return {"upserted": total}


async def zoho_desk_sync_loop() -> None:
    """Background loop: sync every SYNC_INTERVAL_SECONDS. Replay-safe (upsert)."""
    while True:
        try:
            await run_sync()
        except ZohoDeskError as e:
            # Integration not configured / scope issue — warn, keep looping.
            logger.warning("Zoho Desk sync skipped: %s", e.message)
        except Exception:
            logger.error("zoho_desk_sync tick failed", exc_info=True)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
