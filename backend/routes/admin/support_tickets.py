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

import asyncio
import logging
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import require_module
    from ...services import zoho_desk_db
    from ...services import zoho_desk_service as zoho
    from ...services import zoho_ticket_service_area as ticket_area
    from ...services.zoho_desk_service import ZohoDeskError
    from ...utils.audit_logger import log_admin_action
    from ...utils.company_details import CompanyDetails, load_company_details
    from ...utils.rate_limiter import admin_ai_suggest_limit
except ImportError:  # pragma: no cover - direct module import in tests
    import db_supabase
    from dependencies import require_module
    from services import zoho_desk_db
    from services import zoho_desk_service as zoho
    from services import zoho_ticket_service_area as ticket_area
    from services.zoho_desk_service import ZohoDeskError
    from utils.audit_logger import log_admin_action
    from utils.company_details import CompanyDetails, load_company_details
    from utils.rate_limiter import admin_ai_suggest_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/support-tickets", tags=["Admin Support Tickets"])

_CONFIG_TABLE = "zoho_desk_config"
_CONFIG_ID = "default"
_MIRROR_TABLE = "zoho_desk_tickets"


def _err(e: ZohoDeskError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e.message))


import html as _html


def _render_helpdesk_signature(company: CompanyDetails, tagline: str = "") -> str:
    """Build a branded email signature from company settings.

    Uses the same identity source as transactional emails
    (``load_company_details``). All values are HTML-escaped.
    """
    esc = _html.escape
    tagline_html = ""
    if tagline.strip():
        tagline_html = f'<p style="margin:0 0 6px;font-size:12px;color:#6B7280;">{esc(tagline.strip())}</p>'

    links: list[str] = []
    if company.support_email:
        links.append(
            f'<a href="mailto:{esc(company.support_email)}" '
            f'style="color:#D32F2F;text-decoration:none;">{esc(company.support_email)}</a>'
        )
    website = company.contact_line.split(" · ")
    for part in website:
        part = part.strip()
        if part.startswith("http") or part.startswith("www"):
            url = part if part.startswith("http") else f"https://{part}"
            links.append(f'<a href="{esc(url)}" style="color:#D32F2F;text-decoration:none;">{esc(part)}</a>')
            break

    dot = '<span style="color:#E5E7EB;margin:0 4px;">&middot;</span>'
    links_html = dot.join(links)

    return (
        f'<table cellpadding="0" cellspacing="0" border="0" '
        f"style=\"font-family:'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,"
        f"'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        f"<tr>"
        f'<td style="vertical-align:top;padding-right:14px;">'
        f'<img src="{esc(company.logo_url)}" alt="{esc(company.name)}" width="36" '
        f'style="display:block;border:0;outline:none;border-radius:6px;width:36px;height:auto;" />'
        f"</td>"
        f'<td style="vertical-align:top;">'
        f'<p style="margin:0 0 2px;font-size:14px;font-weight:700;color:#1A1A1A;">'
        f"{esc(company.name)} Support</p>"
        f"{tagline_html}"
        f'<p style="margin:0;font-size:12px;">{links_html}</p>'
        f'<p style="margin:8px 0 0;font-size:10px;color:#6B7280;opacity:0.7;">'
        f"{esc(company.identity_line)}</p>"
        f"</td>"
        f"</tr>"
        f"</table>"
    )


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
    auto_sync_enabled: Optional[bool] = None
    data_center: Optional[str] = None
    org_id: Optional[str] = None
    default_department_id: Optional[str] = None
    default_from_email: Optional[str] = None
    helpdesk_signature_enabled: Optional[bool] = None
    helpdesk_email_signature: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None


async def _config_status(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Presence-only view of the config — never leaks secret values."""

    def _has(key: str) -> bool:
        return bool((cfg.get(key) or "").strip())

    sig_enabled = bool(cfg.get("helpdesk_signature_enabled"))
    sig_tagline = cfg.get("helpdesk_email_signature") or ""
    sig_preview = ""
    if sig_enabled:
        try:
            company = await load_company_details()
            sig_preview = _render_helpdesk_signature(company, sig_tagline)
        except Exception:
            logger.warning("Could not render signature preview", exc_info=True)

    return {
        "enabled": bool(cfg.get("enabled")),
        "auto_sync_enabled": bool(cfg.get("auto_sync_enabled")),
        "data_center": cfg.get("data_center") or "ca",
        "org_id": cfg.get("org_id") or "",
        "default_department_id": cfg.get("default_department_id") or "",
        "default_from_email": cfg.get("default_from_email") or "",
        "helpdesk_signature_enabled": sig_enabled,
        "helpdesk_email_signature": sig_tagline,
        "helpdesk_signature_preview": sig_preview,
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
        "last_synced_at": cfg.get("last_synced_at"),
        "last_sync_count": cfg.get("last_sync_count"),
        "updated_at": cfg.get("updated_at"),
    }


@router.get("/config")
async def get_config(admin: dict = Depends(require_module("support_tickets"))):
    cfg = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID}) or {}
    return await _config_status(cfg)


@router.put("/config")
async def update_config(payload: ZohoConfigUpdate, admin: dict = Depends(require_module("support_tickets"))):
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
    return await _config_status(cfg)


@router.post("/sync")
async def trigger_sync(admin: dict = Depends(require_module("support_tickets"))):
    """Run a Zoho -> DB mirror sync now (the background loop also runs every
    ~10 min). Returns how many tickets were upserted."""
    try:
        from ...utils.zoho_desk_sync import run_sync
    except ImportError:
        from utils.zoho_desk_sync import run_sync
    try:
        result = await run_sync()
    except ZohoDeskError as e:
        raise _err(e) from e
    await log_admin_action(admin, "zoho_desk_sync", "zoho_desk_config", _CONFIG_ID, result)
    return result


@router.post("/config/test")
async def test_connection(admin: dict = Depends(require_module("support_tickets"))):
    """Verify the stored credentials by making a lightweight Zoho call."""
    try:
        depts = await zoho.list_departments()
    except ZohoDeskError as e:
        raise _err(e) from e
    return {"ok": True, "departments": (depts or {}).get("data", [])}


# --------------------------------------------------------------------------
# Dashboard + trends
# --------------------------------------------------------------------------


_DASHBOARD_STATUSES = ["Open", "On Hold", "Escalated", "Closed"]


def _aggregate_trends(tickets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a windowed list of Zoho ticket payloads into the trends
    response (volume, breakdowns, resolution stats). Source-agnostic — used for
    both the DB mirror (full history) and the live 100-ticket sample."""
    opened_by_day: Counter = Counter()
    closed_by_day: Counter = Counter()
    by_status: Counter = Counter()
    by_priority: Counter = Counter()
    by_channel: Counter = Counter()
    by_category: Counter = Counter()
    by_classification: Counter = Counter()
    by_tag: Counter = Counter()
    by_contact: Counter = Counter()
    resolution_hours: List[float] = []
    closed_count = 0

    for t in tickets:
        created = t.get("createdTime") or ""
        opened_by_day[created[:10]] += 1
        by_status[t.get("status") or "Unknown"] += 1
        by_priority[t.get("priority") or "None"] += 1
        by_channel[t.get("channel") or "Unknown"] += 1
        by_category[t.get("category") or "Uncategorized"] += 1
        by_classification[t.get("classification") or "None"] += 1

        contact = t.get("contact") or {}
        cname = (
            (contact.get("email") or "").strip()
            or f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
            or (t.get("email") or "").strip()
            or "Unknown"
        )
        by_contact[cname] += 1

        for tag in t.get("tags") or []:
            name = tag.get("name") if isinstance(tag, dict) else str(tag)
            if name:
                by_tag[name] += 1

        closed_at = _parse_zoho_time(t.get("closedTime") or "")
        if closed_at:
            closed_count += 1
            closed_by_day[(t.get("closedTime") or "")[:10]] += 1
            opened_at = _parse_zoho_time(created)
            if opened_at:
                hrs = (closed_at - opened_at).total_seconds() / 3600.0
                if hrs >= 0:
                    resolution_hours.append(hrs)

    all_days = sorted(d for d in set(opened_by_day) | set(closed_by_day) if d and d != "unknown")
    volume = [{"date": d, "opened": opened_by_day.get(d, 0), "closed": closed_by_day.get(d, 0)} for d in all_days]
    avg_res = round(sum(resolution_hours) / len(resolution_hours), 1) if resolution_hours else None
    median_res = round(statistics.median(resolution_hours), 1) if resolution_hours else None

    return {
        "volume": volume,
        "by_status": dict(by_status),
        "by_priority": dict(by_priority),
        "by_channel": dict(by_channel),
        "by_category": dict(by_category),
        "by_classification": dict(by_classification),
        "by_tag": dict(by_tag),
        "top_contacts": [{"name": n, "count": c} for n, c in by_contact.most_common(10)],
        "stats": {
            "opened": len(tickets),
            "closed": closed_count,
            "open_now": len(tickets) - closed_count,
            "avg_resolution_hours": avg_res,
            "median_resolution_hours": median_res,
            "resolved_sample": len(resolution_hours),
        },
    }


@router.get("/dashboard")
async def dashboard(
    department_id: Optional[str] = Query(None),
    admin: dict = Depends(require_module("support_tickets")),
):
    dept = await _resolve_department(department_id)

    # Serve from the mirror only after a full backfill (exact counts, no Zoho
    # credits, no sample cap). Open count uses status_type so custom closed
    # statuses (e.g. 'Resolved') aren't reported as open.
    if await zoho_desk_db.mirror_ready():
        oc = await zoho_desk_db.open_closed_counts(dept)
        by = await zoho_desk_db.count_by_status(dept, _DASHBOARD_STATUSES)
        if oc is not None and by is not None:
            total, closed = oc
            recent = await zoho_desk_db.list_mirror(limit=10, department_id=dept, sort_by="-createdTime") or []
            return {
                "total": total,
                "total_available": True,
                "open": max(0, total - closed),
                "by_status": by[1],
                "recent": recent,
                "sample_size": total,
                "approximate": False,
                "source": "db",
            }

    # Live fallback (before the first sync / migration).
    try:
        sample = await zoho.list_tickets(limit=100, department_id=dept, sort_by="-createdTime")
    except ZohoDeskError as e:
        raise _err(e) from e
    total = None
    try:
        total = await zoho.ticket_count(department_id=dept)
    except ZohoDeskError as e:
        logger.warning("Zoho ticket_count unavailable (scope/credentials?): %s", e.message)
    tickets: List[Dict[str, Any]] = (sample or {}).get("data", [])
    by_status_c: Counter = Counter()
    for t in tickets:
        by_status_c[t.get("status") or "Unknown"] += 1
    open_count = sum(c for s, c in by_status_c.items() if "closed" not in s.lower())
    return {
        "total": total,
        "total_available": total is not None,
        "open": open_count,
        "by_status": dict(by_status_c),
        "recent": tickets[:10],
        "sample_size": len(tickets),
        "approximate": len(tickets) >= 100,
        "source": "live",
    }


@router.get("/trends")
async def trends(
    days: int = Query(14, ge=1, le=90),
    department_id: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    admin: dict = Depends(require_module("support_tickets")),
):
    """Opened-vs-closed timeline, breakdowns, and resolution stats over the
    selected window. Served from the local mirror (full history) once synced;
    otherwise a live 100-ticket sample (flagged approximate)."""
    dept = await _resolve_department(department_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    if await zoho_desk_db.mirror_ready():
        rows = await zoho_desk_db.fetch_window(
            since_iso=cutoff.isoformat(), department_id=dept, assignee_id=assignee_id
        )
        if rows is not None:
            result = _aggregate_trends(rows)
            result["sample_size"] = len(rows)
            result["approximate"] = False
            result["source"] = "db"
            return result

    # Live fallback.
    try:
        page = await zoho.list_tickets(limit=100, department_id=dept, assignee_id=assignee_id, sort_by="-createdTime")
    except ZohoDeskError as e:
        raise _err(e) from e
    raw: List[Dict[str, Any]] = (page or {}).get("data", [])
    tickets = [t for t in raw if (_parse_zoho_time(t.get("createdTime") or "") or cutoff) >= cutoff]
    result = _aggregate_trends(tickets)
    result["sample_size"] = len(tickets)
    result["approximate"] = len(raw) >= 100 and len(tickets) == len(raw)
    result["source"] = "live"
    return result


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


class CreateTicketRequest(BaseModel):
    subject: str = Field(..., min_length=1)
    description: str = ""
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    priority: Optional[str] = None
    channel: str = "Web"
    category: Optional[str] = None
    department_id: Optional[str] = None


@router.post("/tickets")
async def create_ticket(
    payload: CreateTicketRequest,
    admin: dict = Depends(require_module("support_tickets")),
):
    try:
        result = await zoho.create_ticket(
            subject=payload.subject,
            description=payload.description,
            department_id=payload.department_id,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            phone=payload.phone,
            priority=payload.priority,
            channel=payload.channel,
            category=payload.category,
        )
    except ZohoDeskError as e:
        raise _err(e) from e
    await log_admin_action(admin, "zoho_desk_ticket_created", "zoho_desk_ticket", str(result.get("id") or ""), None)
    return result


@router.get("/tickets")
async def tickets(
    from_index: int = Query(1, ge=1, alias="from"),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None),
    department_id: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    sort_by: str = Query("-createdTime"),
    admin: dict = Depends(require_module("support_tickets")),
):
    dept = await _resolve_department(department_id)
    # Serve from the mirror only after a full backfill; else live (so an empty
    # page from a partially-seeded mirror never hides historical tickets).
    if await zoho_desk_db.mirror_ready():
        rows = await zoho_desk_db.list_mirror(
            limit=limit,
            offset=from_index - 1,
            status=status,
            priority=priority,
            channel=channel,
            assignee_id=assignee_id,
            department_id=dept,
            sort_by=sort_by,
        )
        if rows is not None:
            return {"data": rows, "source": "db"}
    try:
        return await zoho.list_tickets(
            from_index=from_index,
            limit=limit,
            status=status,
            department_id=dept,
            assignee_id=assignee_id,
            priority=priority,
            channel=channel,
            sort_by=sort_by,
        )
    except ZohoDeskError as e:
        raise _err(e) from e


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    from_index: int = Query(1, ge=1, alias="from"),
    limit: int = Query(50, ge=1, le=100),
    department_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    admin: dict = Depends(require_module("support_tickets")),
):
    """Account-wide ticket search. Served from the local mirror when synced
    (fast, no Zoho credits); otherwise Zoho /tickets/search."""
    dept = await _resolve_department(department_id)
    if await zoho_desk_db.mirror_ready():
        rows = await zoho_desk_db.list_mirror(
            limit=limit,
            offset=from_index - 1,
            search=q,
            status=status,
            priority=priority,
            assignee_id=assignee_id,
            department_id=dept,
            sort_by="-createdTime",
        )
        if rows is not None:
            return {"data": rows, "source": "db"}
    try:
        return await zoho.search_tickets(
            query=q,
            from_index=from_index,
            limit=limit,
            department_id=dept,
            status=status,
            priority=priority,
            assignee_id=assignee_id,
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


@router.get("/tickets/{ticket_id}/threads/{thread_id}")
async def ticket_thread_detail(
    ticket_id: str, thread_id: str, admin: dict = Depends(require_module("support_tickets"))
):
    try:
        return await zoho.get_thread(ticket_id, thread_id)
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
    content = payload.content
    if cfg.get("helpdesk_signature_enabled") and payload.channel.upper() == "EMAIL":
        company = await load_company_details()
        tagline = (cfg.get("helpdesk_email_signature") or "").strip()
        sig_html = _render_helpdesk_signature(company, tagline)
        content = f'{content}<br><br><div style="margin-top:16px;border-top:1px solid #e5e7eb;padding-top:12px;">{sig_html}</div>'
    try:
        result = await zoho.send_reply(
            ticket_id,
            content=content,
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
    category: Optional[str] = None
    subCategory: Optional[str] = None
    classification: Optional[str] = None


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


class TicketTagsUpdate(BaseModel):
    add: List[str] = Field(default_factory=list)
    remove: List[str] = Field(default_factory=list)


@router.post("/tickets/{ticket_id}/tags")
async def update_ticket_tags(
    ticket_id: str,
    payload: TicketTagsUpdate,
    admin: dict = Depends(require_module("support_tickets")),
):
    if not payload.add and not payload.remove:
        raise HTTPException(status_code=400, detail="No tag changes supplied")
    try:
        if payload.remove:
            await zoho.remove_tags(ticket_id, payload.remove)
        if payload.add:
            await zoho.add_tags(ticket_id, payload.add)
    except ZohoDeskError as e:
        raise _err(e) from e
    await log_admin_action(
        admin,
        "zoho_desk_ticket_tags",
        "zoho_desk_ticket",
        ticket_id,
        {"added": payload.add, "removed": payload.remove},
    )
    return {"ok": True}


# --------------------------------------------------------------------------
# Service area (Spinr-local, not synced to Zoho)
# --------------------------------------------------------------------------


@router.get("/service-areas")
async def service_areas(admin: dict = Depends(require_module("support_tickets"))):
    """Active service areas (id + name) for the Help Desk assign dropdown.

    Lightweight + gated by the support_tickets module so support staff can
    assign without needing the full service_areas admin module."""
    rows = await db_supabase.get_rows(
        "service_areas", {"is_active": True}, order="name", columns="id,name,city,province"
    )
    return {"data": rows or []}


@router.get("/tickets/{ticket_id}/service-area")
async def get_ticket_service_area(ticket_id: str, admin: dict = Depends(require_module("support_tickets"))):
    """Current service-area assignment for a ticket. When unassigned, attempts
    an auto-assign from the contact's ride history; if none is found, returns
    ``needs_assignment=True`` so the UI highlights it (assignment is optional).

    Resolves from the local mirror (the contact email is all that's needed) so a
    ticket view spends no Zoho API credit; falls back to a live fetch only when
    the ticket isn't mirrored yet (pre-backfill)."""
    ticket = await db_supabase.find_one(_MIRROR_TABLE, {"zoho_id": ticket_id})
    if not ticket:
        try:
            ticket = await zoho.get_ticket(ticket_id)
        except ZohoDeskError as e:
            raise _err(e) from e
    return await ticket_area.assignment_view(ticket_id, ticket or {})


class ServiceAreaAssign(BaseModel):
    service_area_id: Optional[str] = None  # None clears the assignment


@router.put("/tickets/{ticket_id}/service-area")
async def set_ticket_service_area(
    ticket_id: str,
    payload: ServiceAreaAssign,
    admin: dict = Depends(require_module("support_tickets")),
):
    """Manually assign (or clear) a ticket's service area. Stored locally only —
    Zoho has no service-area concept — and preserved across syncs."""
    area_id = (payload.service_area_id or "").strip() or None
    if area_id:
        area = await db_supabase.find_one("service_areas", {"id": area_id})
        if not area:
            raise HTTPException(status_code=404, detail="Service area not found")
    result = await ticket_area.set_ticket_area(
        ticket_id, area_id, source="manual", assigned_by=str(admin.get("id") or ""), upsert=True
    )
    await log_admin_action(
        admin,
        "zoho_desk_ticket_service_area",
        "zoho_desk_ticket",
        ticket_id,
        {"service_area_id": area_id},
    )
    return {**result, "needs_assignment": not result.get("service_area_id"), "suggested": None}


# --------------------------------------------------------------------------
# AI reply suggestion (draft for a human to review/edit/send)
# --------------------------------------------------------------------------

_AI_ERROR_STATUS = {"ai_disabled": 409, "ai_misconfigured": 502, "provider_error": 502, "empty": 502}

# How many of the most recent conversation entries to hydrate with their full
# body before drafting — matches the assistant's context budget so we never
# spend a Zoho call (or tokens) on threads the draft won't read.
try:
    from ...ai.support_assistant import _MAX_THREAD_MESSAGES as _AI_MAX_THREADS
except ImportError:  # pragma: no cover - direct module import in tests
    from ai.support_assistant import _MAX_THREAD_MESSAGES as _AI_MAX_THREADS


async def _hydrate_thread_bodies(ticket_id: str, conversations: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Replace each email thread's truncated ``summary`` with its full body.

    Zoho's /conversations list returns only a short ``summary`` per email
    thread; the complete mail text lives on the per-thread resource. Without
    this, the AI drafts from snippets and misses the actual conversation. We
    hydrate only the most recent entries (the ones the assistant keeps) and
    fetch them concurrently. Comments already carry full content, and any
    per-thread fetch failure falls back to the summary rather than failing the
    whole suggestion.
    """
    recent = (conversations or [])[-_AI_MAX_THREADS:]

    async def _full(m: Dict[str, Any]) -> Dict[str, Any]:
        if m.get("type") == "comment":
            return m
        thread_id = str(m.get("id") or "")
        if not thread_id:
            return m
        try:
            full = await zoho.get_thread(ticket_id, thread_id)
        except ZohoDeskError as e:
            logger.warning("Could not hydrate thread %s of ticket %s: %s", thread_id, ticket_id, e.message)
            return m
        body = (full or {}).get("content") or (full or {}).get("plainText")
        return {**m, "content": body} if body else m

    return list(await asyncio.gather(*[_full(m) for m in recent]))


class AiSuggestRequest(BaseModel):
    # Optional agent guidance — what the reply should say / the decision to
    # convey. Steers the draft; the agent still reviews and edits before sending.
    instruction: Optional[str] = Field(None, max_length=4000)


@router.post("/tickets/{ticket_id}/ai-suggest-reply")
@admin_ai_suggest_limit
async def ai_suggest_reply(
    request: Request,
    ticket_id: str,
    payload: Optional[AiSuggestRequest] = None,
    admin: dict = Depends(require_module("support_tickets")),
):
    """Draft a reply using the AI assistant. Reads the full ticket mail chain
    (not just thread summaries) and optionally follows the agent's guidance.
    Returns the suggestion only — the agent reviews/edits and sends it via the
    normal /reply endpoint. Nothing is sent to the customer here."""
    try:
        from ...ai.support_assistant import SupportAssistantError, suggest_ticket_reply
    except ImportError:
        from ai.support_assistant import SupportAssistantError, suggest_ticket_reply

    # The ticket, its conversation list, and its (Spinr-local) service area are
    # independent reads — fetch them concurrently so the agent waits on one
    # round-trip, not three, before the LLM call. Hydration depends on the
    # conversation list, so it chains after.
    try:
        ticket, threads, area = await asyncio.gather(
            zoho.get_ticket(ticket_id),
            zoho.get_ticket_threads(ticket_id),
            ticket_area.get_ticket_area(ticket_id),
        )
        thread = await _hydrate_thread_bodies(ticket_id, (threads or {}).get("data"))
    except ZohoDeskError as e:
        raise _err(e) from e

    try:
        result = await suggest_ticket_reply(
            ticket=ticket or {},
            thread=thread,
            service_area_name=area.get("service_area_name"),
            instruction=payload.instruction if payload else None,
        )
    except SupportAssistantError as e:
        raise HTTPException(status_code=_AI_ERROR_STATUS.get(e.code, 502), detail=e.message) from e

    await log_admin_action(
        admin,
        "zoho_desk_ticket_ai_suggest",
        "zoho_desk_ticket",
        ticket_id,
        {
            "provider": result.get("provider"),
            "model": result.get("model"),
            "had_instruction": bool(payload and (payload.instruction or "").strip()),
        },
    )
    return result
