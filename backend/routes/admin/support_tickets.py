"""
Admin support-tickets routes — Zoho Desk proxy.

Mounted under /api/admin/support-tickets and gated by the
``support_tickets`` RBAC module (see routes/admin/__init__.py). Staff with
that module can view the ticket dashboard/trends, open and reply to
tickets, change status/priority, and (re)assign to agents — all proxied
live to Zoho Desk via services/zoho_desk_service.py.

Connection config (OAuth client id/secret, refresh token, org, data
center) is managed here too, but the secret values are write-only:
GET /config returns presence flags, never the secrets themselves.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user, require_module
    from ...services import zoho_desk_service as zoho
    from ...services.zoho_desk_service import ZohoDeskError
    from ...utils.audit_logger import log_admin_action
except ImportError:  # pragma: no cover - direct module import in tests
    import db_supabase
    from dependencies import get_admin_user, require_module
    from services import zoho_desk_service as zoho
    from services.zoho_desk_service import ZohoDeskError
    from utils.audit_logger import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/support-tickets", tags=["Admin Support Tickets"])

_CONFIG_TABLE = "zoho_desk_config"
_CONFIG_ID = "default"


def _err(e: ZohoDeskError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e.message))


async def _resolve_department(department_id: Optional[str]) -> Optional[str]:
    """Fall back to the admin-configured default department when the caller
    does not pass one explicitly, so a saved default actually scopes the
    dashboard / trends / ticket list in multi-department accounts."""
    if department_id:
        return department_id
    cfg = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID}) or {}
    dep = (cfg.get("default_department_id") or "").strip()
    return dep or None


def _parse_zoho_time(value: str):
    """Parse a Zoho ISO timestamp (e.g. '2026-06-01T12:00:00.000Z') to an
    aware datetime, or None if unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# --------------------------------------------------------------------------
# Connection config (secrets are write-only)
# --------------------------------------------------------------------------


class ZohoConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    data_center: Optional[str] = None
    org_id: Optional[str] = None
    default_department_id: Optional[str] = None
    default_from_email: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None


def _config_status(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Presence-only view of the config — never leaks secret values."""

    def _has(key: str) -> bool:
        return bool((cfg.get(key) or "").strip())

    return {
        "enabled": bool(cfg.get("enabled")),
        "data_center": cfg.get("data_center") or "ca",
        "org_id": cfg.get("org_id") or "",
        "default_department_id": cfg.get("default_department_id") or "",
        "default_from_email": cfg.get("default_from_email") or "",
        "has_client_id": _has("client_id"),
        "has_client_secret": _has("client_secret"),
        "has_refresh_token": _has("refresh_token"),
        "connected": bool(
            cfg.get("enabled")
            and _has("client_id")
            and _has("client_secret")
            and _has("refresh_token")
            and _has("org_id")
        ),
        "updated_at": cfg.get("updated_at"),
    }


@router.get("/config")
async def get_config(admin: dict = Depends(get_admin_user)):
    cfg = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID}) or {}
    return _config_status(cfg)


@router.put("/config")
async def update_config(payload: ZohoConfigUpdate, admin: dict = Depends(get_admin_user)):
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields supplied")
    # Empty strings for secret fields would wipe a working credential by
    # accident — drop them so callers must send a real value to change one.
    for secret in ("client_secret", "refresh_token", "client_id"):
        if secret in fields and not (fields[secret] or "").strip():
            fields.pop(secret)
    if "data_center" in fields:
        fields["data_center"] = (fields["data_center"] or "ca").strip().lower()

    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Changing any credential invalidates the cached access token.
    if {"client_id", "client_secret", "refresh_token", "data_center"} & set(fields):
        fields["access_token"] = None
        fields["access_token_expires_at"] = None

    await db_supabase.update_one(_CONFIG_TABLE, {"id": _CONFIG_ID}, fields, upsert=True)
    await log_admin_action(
        admin,
        "zoho_desk_config_updated",
        "zoho_desk_config",
        _CONFIG_ID,
        # Audit which keys changed — never the secret values themselves.
        {"fields_changed": [k for k in fields if k not in ("updated_at",)]},
    )
    cfg = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID}) or {}
    return _config_status(cfg)


@router.post("/config/test")
async def test_connection(admin: dict = Depends(get_admin_user)):
    """Verify the stored credentials by making a lightweight Zoho call."""
    try:
        depts = await zoho.list_departments()
    except ZohoDeskError as e:
        raise _err(e) from e
    return {"ok": True, "departments": (depts or {}).get("data", [])}


# --------------------------------------------------------------------------
# Dashboard + trends
# --------------------------------------------------------------------------


@router.get("/dashboard")
async def dashboard(
    department_id: Optional[str] = Query(None),
    admin: dict = Depends(require_module("support_tickets")),
):
    dept = await _resolve_department(department_id)
    try:
        # `total` is an accurate count from Zoho's /ticketsCount. The
        # per-status breakdown is derived from a recent sample because
        # /ticketsCount does not support a `status` filter and the REST tier
        # has no generic group-by endpoint we can rely on across accounts.
        total = await zoho.ticket_count(department_id=dept)
        sample = await zoho.list_tickets(limit=100, department_id=dept, sort_by="-createdTime")
    except ZohoDeskError as e:
        raise _err(e) from e

    tickets: List[Dict[str, Any]] = (sample or {}).get("data", [])
    by_status: Counter = Counter()
    for t in tickets:
        by_status[t.get("status") or "Unknown"] += 1

    open_count = sum(c for s, c in by_status.items() if "closed" not in s.lower())
    return {
        "total": total,
        "open": open_count,
        "by_status": dict(by_status),
        "recent": tickets[:10],
        "sample_size": len(tickets),
        "approximate": len(tickets) >= 100,
    }


@router.get("/trends")
async def trends(
    days: int = Query(14, ge=1, le=90),
    department_id: Optional[str] = Query(None),
    admin: dict = Depends(require_module("support_tickets")),
):
    """Time-series + breakdowns derived from the most recent tickets.

    Zoho's API does not expose a generic aggregate report endpoint on the
    REST tier, so we aggregate the latest page of tickets client-side, keeping
    only those created within the selected window. This is approximate when
    more than 100 tickets fall inside the window, and is labelled as such.
    """
    dept = await _resolve_department(department_id)
    try:
        page = await zoho.list_tickets(limit=100, department_id=dept, sort_by="-createdTime")
    except ZohoDeskError as e:
        raise _err(e) from e

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    raw: List[Dict[str, Any]] = (page or {}).get("data", [])
    # Honor the requested window: drop tickets created before the cutoff so
    # the Last 7/14/30/90-day selector produces the report it promises.
    tickets = [t for t in raw if (_parse_zoho_time(t.get("createdTime") or "") or cutoff) >= cutoff]

    by_day: Counter = Counter()
    by_status: Counter = Counter()
    by_priority: Counter = Counter()
    by_channel: Counter = Counter()

    for t in tickets:
        created = t.get("createdTime") or ""
        day = created[:10] if len(created) >= 10 else "unknown"
        by_day[day] += 1
        by_status[t.get("status") or "Unknown"] += 1
        by_priority[t.get("priority") or "None"] += 1
        by_channel[t.get("channel") or "Unknown"] += 1

    volume = [{"date": d, "count": c} for d, c in sorted(by_day.items()) if d != "unknown"]
    return {
        "sample_size": len(tickets),
        # Approximate when the raw page was capped AND everything we fetched is
        # inside the window (i.e. there may be more we didn't see).
        "approximate": len(raw) >= 100 and len(tickets) == len(raw),
        "volume": volume,
        "by_status": dict(by_status),
        "by_priority": dict(by_priority),
        "by_channel": dict(by_channel),
    }


# --------------------------------------------------------------------------
# Tickets
# --------------------------------------------------------------------------


@router.get("/agents")
async def agents(admin: dict = Depends(require_module("support_tickets"))):
    try:
        return await zoho.list_agents()
    except ZohoDeskError as e:
        raise _err(e) from e


@router.get("/departments")
async def departments(admin: dict = Depends(require_module("support_tickets"))):
    try:
        return await zoho.list_departments()
    except ZohoDeskError as e:
        raise _err(e) from e


@router.get("/tickets")
async def tickets(
    from_index: int = Query(1, ge=1, alias="from"),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None),
    department_id: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    sort_by: str = Query("-createdTime"),
    admin: dict = Depends(require_module("support_tickets")),
):
    dept = await _resolve_department(department_id)
    try:
        return await zoho.list_tickets(
            from_index=from_index,
            limit=limit,
            status=status,
            department_id=dept,
            assignee_id=assignee_id,
            sort_by=sort_by,
        )
    except ZohoDeskError as e:
        raise _err(e) from e


@router.get("/tickets/{ticket_id}")
async def ticket_detail(ticket_id: str, admin: dict = Depends(require_module("support_tickets"))):
    try:
        return await zoho.get_ticket(ticket_id)
    except ZohoDeskError as e:
        raise _err(e) from e


@router.get("/tickets/{ticket_id}/threads")
async def ticket_threads(ticket_id: str, admin: dict = Depends(require_module("support_tickets"))):
    try:
        return await zoho.get_ticket_threads(ticket_id)
    except ZohoDeskError as e:
        raise _err(e) from e


class ReplyRequest(BaseModel):
    content: str = Field(..., min_length=1)
    to: Optional[str] = None
    channel: str = "EMAIL"


@router.post("/tickets/{ticket_id}/reply")
async def reply_ticket(
    ticket_id: str,
    payload: ReplyRequest,
    admin: dict = Depends(require_module("support_tickets")),
):
    # EMAIL replies need a configured portal from-address or Zoho rejects them.
    cfg = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID}) or {}
    from_email = (cfg.get("default_from_email") or "").strip() or None
    if payload.channel.upper() == "EMAIL" and not from_email:
        raise HTTPException(
            status_code=400,
            detail="No reply-from email configured. Set a default reply-from address in Help Desk settings.",
        )
    try:
        result = await zoho.send_reply(
            ticket_id,
            content=payload.content,
            to=payload.to,
            from_email=from_email,
            channel=payload.channel,
        )
    except ZohoDeskError as e:
        raise _err(e) from e
    await log_admin_action(admin, "zoho_desk_ticket_reply", "zoho_desk_ticket", ticket_id, {"channel": payload.channel})
    return result


class CommentRequest(BaseModel):
    content: str = Field(..., min_length=1)
    is_public: bool = False


@router.post("/tickets/{ticket_id}/comment")
async def comment_ticket(
    ticket_id: str,
    payload: CommentRequest,
    admin: dict = Depends(require_module("support_tickets")),
):
    try:
        result = await zoho.add_comment(ticket_id, content=payload.content, is_public=payload.is_public)
    except ZohoDeskError as e:
        raise _err(e) from e
    await log_admin_action(admin, "zoho_desk_ticket_comment", "zoho_desk_ticket", ticket_id, None)
    return result


class TicketUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assigneeId: Optional[str] = None
    departmentId: Optional[str] = None


@router.patch("/tickets/{ticket_id}")
async def patch_ticket(
    ticket_id: str,
    payload: TicketUpdate,
    admin: dict = Depends(require_module("support_tickets")),
):
    fields = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields supplied")
    try:
        result = await zoho.update_ticket(ticket_id, fields)
    except ZohoDeskError as e:
        raise _err(e) from e
    await log_admin_action(admin, "zoho_desk_ticket_updated", "zoho_desk_ticket", ticket_id, {"fields": list(fields)})
    return result
