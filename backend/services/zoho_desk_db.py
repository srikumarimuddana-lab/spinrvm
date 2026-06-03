"""
Read helpers for the local Zoho Desk mirror (``zoho_desk_tickets``, migration
123). The Help Desk serves lists / search / dashboards / trends from here so it
doesn't spend Zoho API credits on every page view. Each row stores the full
Zoho payload in ``raw``, so callers get back the exact ticket shape the
frontend already handles.

All functions return ``None`` (not an exception) when the mirror is unavailable
or empty, so routes can transparently fall back to the live API before the
first sync / migration.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover
    import db_supabase

logger = logging.getLogger(__name__)

_TABLE = "zoho_desk_tickets"
_PAGE = 1000  # Supabase per-request row cap


async def mirror_count() -> Optional[int]:
    """Total rows in the mirror, or None if the table is unavailable."""
    if not db_supabase.supabase:
        return None

    def _fn():
        res = db_supabase.supabase.table(_TABLE).select("zoho_id", count="exact").limit(1).execute()
        return getattr(res, "count", None)

    try:
        return await db_supabase.run_sync(_fn)
    except Exception as e:
        logger.warning("Zoho mirror unavailable (not migrated yet?): %s", e)
        return None


def _apply_filters(q, *, status, priority, channel, assignee_id, department_id):
    if status:
        q = q.eq("status", status)
    if priority:
        q = q.eq("priority", priority)
    if channel:
        q = q.eq("channel", channel)
    if assignee_id:
        q = q.eq("assignee_id", assignee_id)
    if department_id:
        q = q.eq("department_id", department_id)
    return q


async def list_mirror(
    *,
    limit: int = 25,
    offset: int = 0,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    channel: Optional[str] = None,
    assignee_id: Optional[str] = None,
    department_id: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "-createdTime",
) -> Optional[List[Dict[str, Any]]]:
    """Return a page of tickets (raw Zoho payloads) from the mirror, or None
    if the mirror is unavailable."""
    if not db_supabase.supabase:
        return None
    col = "modified_time" if "modifiedTime" in sort_by else "created_time"
    desc = sort_by.startswith("-")

    def _fn():
        q = db_supabase.supabase.table(_TABLE).select("raw")
        q = _apply_filters(
            q,
            status=status,
            priority=priority,
            channel=channel,
            assignee_id=assignee_id,
            department_id=department_id,
        )
        s = (search or "").strip()
        if s:
            esc = s.replace(",", " ")
            q = q.or_(
                f"ticket_number.ilike.%{esc}%,subject.ilike.%{esc}%,"
                f"contact_email.ilike.%{esc}%,contact_name.ilike.%{esc}%"
            )
        q = q.order(col, desc=desc).range(offset, offset + limit - 1)
        return q.execute()

    try:
        res = await db_supabase.run_sync(_fn)
        rows = db_supabase._rows_from_res(res)
        return [r.get("raw") for r in rows if r.get("raw")]
    except Exception as e:
        logger.warning("Zoho mirror list failed, falling back to live: %s", e)
        return None


async def count_by_status(department_id: Optional[str], statuses: List[str]) -> Optional[Tuple[int, Dict[str, int]]]:
    """Exact (total, {status: count}) from the mirror, or None if unavailable."""
    if not db_supabase.supabase:
        return None

    def _count(status: Optional[str]):
        q = db_supabase.supabase.table(_TABLE).select("zoho_id", count="exact").limit(1)
        if department_id:
            q = q.eq("department_id", department_id)
        if status:
            q = q.eq("status", status)
        return getattr(q.execute(), "count", 0) or 0

    try:
        total = await db_supabase.run_sync(lambda: _count(None))
        by_status: Dict[str, int] = {}
        for s in statuses:
            by_status[s] = await db_supabase.run_sync(lambda s=s: _count(s))
        return total, by_status
    except Exception as e:
        logger.warning("Zoho mirror count failed, falling back to live: %s", e)
        return None


async def fetch_window(
    *, since_iso: str, department_id: Optional[str], assignee_id: Optional[str], max_rows: int = 5000
) -> Optional[List[Dict[str, Any]]]:
    """All tickets created since ``since_iso`` (raw payloads), paged. None if
    the mirror is unavailable."""
    if not db_supabase.supabase:
        return None

    out: List[Dict[str, Any]] = []
    offset = 0
    try:
        while offset < max_rows:

            def _fn(off=offset):
                q = db_supabase.supabase.table(_TABLE).select("raw").gte("created_time", since_iso)
                if department_id:
                    q = q.eq("department_id", department_id)
                if assignee_id:
                    q = q.eq("assignee_id", assignee_id)
                return q.order("created_time", desc=True).range(off, off + _PAGE - 1).execute()

            res = await db_supabase.run_sync(_fn)
            rows = db_supabase._rows_from_res(res)
            out.extend(r.get("raw") for r in rows if r.get("raw"))
            if len(rows) < _PAGE:
                break
            offset += _PAGE
        return out
    except Exception as e:
        logger.warning("Zoho mirror window failed, falling back to live: %s", e)
        return None
