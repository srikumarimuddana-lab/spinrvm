"""
ZohoDeskService — thin async proxy over the Zoho Desk REST API.

The admin panel does NOT mirror tickets locally; it proxies the Zoho Desk
API live through the backend so staff always see current state. This module
owns:

  * the OAuth2 refresh-token grant (exchange refresh_token -> access_token),
  * an in-DB cache of the short-lived access token (zoho_desk_config row),
  * a generic authenticated request helper that refreshes-and-retries once
    on a 401, and
  * the small set of endpoints the admin pages need (tickets, threads,
    replies, status/assignee updates, agents, departments, counts).

Config + tokens live in the single-row ``zoho_desk_config`` table
(migration 121). The backend's service-role key bypasses RLS; the table
is never exposed to the frontend.

Zoho data centers expose different domains. ``data_center`` in the config
selects them. Spinr is Canadian, so the default is the Canada DC.

References: https://desk.zoho.com/DeskAPIDocument
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover - allow direct module imports in tests
    import db_supabase

logger = logging.getLogger(__name__)

_CONFIG_TABLE = "zoho_desk_config"
_CONFIG_ID = "default"
_HTTP_TIMEOUT = 15.0
# Refresh the access token this many seconds before its real expiry so an
# in-flight request never races the boundary.
_EXPIRY_SKEW_SECONDS = 60

# data_center code -> (accounts base, desk API base). Zoho hosts each region
# on a distinct domain; using the wrong one yields INVALID_OAUTH errors.
_DATA_CENTERS: Dict[str, Dict[str, str]] = {
    "com": {"accounts": "https://accounts.zoho.com", "desk": "https://desk.zoho.com"},
    "ca": {"accounts": "https://accounts.zohocloud.ca", "desk": "https://desk.zohocloud.ca"},
    "eu": {"accounts": "https://accounts.zoho.eu", "desk": "https://desk.zoho.eu"},
    "in": {"accounts": "https://accounts.zoho.in", "desk": "https://desk.zoho.in"},
    "com.au": {"accounts": "https://accounts.zoho.com.au", "desk": "https://desk.zoho.com.au"},
    "jp": {"accounts": "https://accounts.zoho.jp", "desk": "https://desk.zoho.jp"},
}


class ZohoDeskError(Exception):
    """Raised when Zoho Desk is misconfigured or returns an error.

    ``status`` is the HTTP status the route layer should surface (502 for
    upstream failures, 503 when the integration is not configured).
    """

    def __init__(self, message: str, status: int = 502, details: Optional[Any] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details


def _dc_domains(data_center: str) -> Dict[str, str]:
    dc = (data_center or "ca").strip().lower()
    if dc not in _DATA_CENTERS:
        raise ZohoDeskError(
            f"Unknown Zoho data center '{data_center}'. Expected one of: {', '.join(sorted(_DATA_CENTERS))}.",
            status=503,
        )
    return _DATA_CENTERS[dc]


async def _load_config() -> Dict[str, Any]:
    row = await db_supabase.find_one(_CONFIG_TABLE, {"id": _CONFIG_ID})
    if not row:
        raise ZohoDeskError(
            "Zoho Desk is not configured. Connect it from the admin panel first.",
            status=503,
        )
    return row


def _require_connected(cfg: Dict[str, Any]) -> None:
    if not cfg.get("enabled"):
        raise ZohoDeskError("Zoho Desk integration is disabled.", status=503)
    missing = [k for k in ("client_id", "client_secret", "refresh_token", "org_id") if not (cfg.get(k) or "").strip()]
    if missing:
        raise ZohoDeskError(
            f"Zoho Desk is not fully configured (missing: {', '.join(missing)}).",
            status=503,
        )


def _token_is_fresh(cfg: Dict[str, Any]) -> bool:
    token = (cfg.get("access_token") or "").strip()
    if not token:
        return False
    expires_at = cfg.get("access_token_expires_at")
    if not expires_at:
        return False
    try:
        if isinstance(expires_at, str):
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        else:
            exp = expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return datetime.now(timezone.utc) < exp


async def _refresh_access_token(cfg: Dict[str, Any]) -> str:
    """Exchange the stored refresh token for a new access token and persist it.

    Zoho refresh tokens are long-lived; access tokens expire in ~1h. We store
    the new access token + expiry back on the config row so other replicas and
    later requests reuse it instead of refreshing on every call.
    """
    domains = _dc_domains(cfg.get("data_center", "ca"))
    params = {
        "refresh_token": cfg["refresh_token"],
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "grant_type": "refresh_token",
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(f"{domains['accounts']}/oauth/v2/token", params=params)
    except httpx.HTTPError as e:
        logger.error("Zoho Desk token refresh transport error: %s", e)
        raise ZohoDeskError("Could not reach Zoho accounts to refresh token.", status=502) from e

    data: Dict[str, Any] = {}
    try:
        data = resp.json()
    except ValueError:
        pass

    access_token = data.get("access_token")
    if resp.status_code != 200 or not access_token:
        # Zoho returns 200 with an {"error": ...} body for bad grants.
        err = data.get("error") or resp.text
        logger.error("Zoho Desk token refresh failed (%s): %s", resp.status_code, err)
        raise ZohoDeskError(f"Zoho token refresh failed: {err}", status=502, details=err)

    expires_in = int(data.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - _EXPIRY_SKEW_SECONDS))

    await db_supabase.update_one(
        _CONFIG_TABLE,
        {"id": _CONFIG_ID},
        {
            "access_token": access_token,
            "access_token_expires_at": expires_at.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    cfg["access_token"] = access_token
    cfg["access_token_expires_at"] = expires_at.isoformat()
    return access_token


async def _get_access_token(cfg: Dict[str, Any], force: bool = False) -> str:
    if not force and _token_is_fresh(cfg):
        return cfg["access_token"]
    return await _refresh_access_token(cfg)


async def _request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Any:
    """Authenticated Zoho Desk request. Refreshes the token and retries once
    on a 401 (expired/revoked access token)."""
    cfg = await _load_config()
    _require_connected(cfg)
    domains = _dc_domains(cfg.get("data_center", "ca"))
    url = f"{domains['desk']}{path}"

    async def _do(token: str) -> httpx.Response:
        headers = {
            "Authorization": f"Zoho-oauthtoken {token}",
            "orgId": str(cfg["org_id"]),
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            return await client.request(method, url, params=params, json=json_body, headers=headers)

    token = await _get_access_token(cfg)
    try:
        resp = await _do(token)
        if resp.status_code == 401:
            # Access token expired/revoked mid-flight — refresh once and retry.
            token = await _get_access_token(cfg, force=True)
            resp = await _do(token)
    except httpx.HTTPError as e:
        logger.error("Zoho Desk request transport error (%s %s): %s", method, path, e)
        raise ZohoDeskError("Could not reach Zoho Desk.", status=502) from e

    if resp.status_code == 204:
        return None
    if resp.status_code >= 400:
        body: Any = resp.text
        try:
            body = resp.json()
        except ValueError:
            pass
        logger.error("Zoho Desk API error (%s %s) -> %s: %s", method, path, resp.status_code, body)
        raise ZohoDeskError(
            f"Zoho Desk returned {resp.status_code}.",
            status=502,
            details=body,
        )
    try:
        return resp.json()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Public API used by routes/admin/support_tickets.py
# --------------------------------------------------------------------------


async def list_tickets(
    *,
    from_index: int = 1,
    limit: int = 50,
    status: Optional[str] = None,
    department_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    priority: Optional[str] = None,
    channel: Optional[str] = None,
    sort_by: str = "-createdTime",
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "from": max(1, from_index),
        "limit": min(max(1, limit), 100),
        "sortBy": sort_by,
        "include": "contacts,assignee",
    }
    if status:
        params["status"] = status
    if department_id:
        # The List Tickets endpoint filters by `departmentIds` (plural,
        # comma-separated) — `departmentId` (singular) is silently ignored
        # here (it's only valid on the count endpoints).
        params["departmentIds"] = department_id
    if assignee_id:
        params["assignee"] = assignee_id
    if priority:
        params["priority"] = priority
    if channel:
        params["channel"] = channel
    data = await _request("GET", "/api/v1/tickets", params=params)
    return data or {"data": []}


async def search_tickets(
    *,
    query: str,
    from_index: int = 1,
    limit: int = 50,
    department_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assignee_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Server-side ticket search across the whole account (not just one page).

    Uses Zoho's /tickets/search. A purely numeric query is treated as a ticket
    number; otherwise it's a wildcard keyword (`_all`) match over subject,
    contact, etc. Requires the Desk.search.READ scope. Note: search uses the
    singular `departmentId`/`assigneeId` params (unlike the list endpoint)."""
    params: Dict[str, Any] = {
        "from": max(1, from_index),
        "limit": min(max(1, limit), 100),
        "include": "contacts,assignee",
    }
    q = (query or "").strip()
    if q.isdigit():
        params["ticketNumber"] = q
    elif q:
        params["_all"] = f"*{q}*"
    if department_id:
        params["departmentId"] = department_id
    if status:
        params["status"] = status
    if priority:
        params["priority"] = priority
    if assignee_id:
        params["assigneeId"] = assignee_id
    data = await _request("GET", "/api/v1/tickets/search", params=params)
    return data or {"data": []}


async def get_ticket(ticket_id: str) -> Dict[str, Any]:
    return await _request("GET", f"/api/v1/tickets/{ticket_id}", params={"include": "contacts,assignee,team"})


async def get_default_department_id() -> Optional[str]:
    """The admin-configured default department, used when a caller (e.g. an
    app event creating a ticket) doesn't specify one."""
    cfg = await _load_config()
    return (cfg.get("default_department_id") or "").strip() or None


async def create_ticket(
    *,
    subject: str,
    description: str = "",
    department_id: Optional[str] = None,
    email: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    phone: Optional[str] = None,
    priority: Optional[str] = None,
    channel: str = "Web",
    category: Optional[str] = None,
    contact_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a Zoho Desk ticket. ``departmentId`` is required by Zoho — falls
    back to the configured default. A contact is required: pass ``contact_id``
    for an existing contact, or contact details to create/link inline."""
    dept = department_id or await get_default_department_id()
    if not dept:
        raise ZohoDeskError(
            "No department for the ticket. Set a default department in Help Desk settings.",
            status=400,
        )
    body: Dict[str, Any] = {
        "subject": subject or "(no subject)",
        "departmentId": dept,
        "description": description or "",
        "channel": channel,
    }
    if priority:
        body["priority"] = priority
    if category:
        body["category"] = category
    if contact_id:
        body["contactId"] = contact_id
    else:
        # Inline contact — Zoho creates/links it. lastName is required by Zoho
        # for a contact, so synthesise one from the email if missing.
        body["contact"] = {
            "lastName": last_name or (email.split("@")[0] if email else "Customer"),
            "firstName": first_name or "",
            "email": email or "",
            "phone": phone or "",
        }
    if extra:
        body.update(extra)
    return await _request("POST", "/api/v1/tickets", json_body=body)


async def get_ticket_threads(ticket_id: str) -> Dict[str, Any]:
    """Conversation thread (replies + comments) for a ticket."""
    data = await _request("GET", f"/api/v1/tickets/{ticket_id}/conversations", params={"limit": 100})
    return data or {"data": []}


async def get_thread(ticket_id: str, thread_id: str) -> Dict[str, Any]:
    """Full content of a single email thread. The /conversations list only
    returns a truncated `summary` per thread; the full `content`/`plainText`
    lives on the individual thread resource."""
    return await _request("GET", f"/api/v1/tickets/{ticket_id}/threads/{thread_id}")


async def send_reply(
    ticket_id: str,
    *,
    content: str,
    to: Optional[str] = None,
    from_email: Optional[str] = None,
    channel: str = "EMAIL",
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "channel": channel,
        "content": content,
        "contentType": "html",
        "isForward": False,
    }
    if to:
        body["to"] = to
    # EMAIL replies require `fromEmailAddress` to be one of the portal's
    # configured support addresses, or Zoho rejects the reply.
    if from_email:
        body["fromEmailAddress"] = from_email
    return await _request("POST", f"/api/v1/tickets/{ticket_id}/sendReply", json_body=body)


async def add_comment(ticket_id: str, *, content: str, is_public: bool = False) -> Dict[str, Any]:
    body = {"content": content, "contentType": "html", "isPublic": is_public}
    return await _request("POST", f"/api/v1/tickets/{ticket_id}/comments", json_body=body)


async def update_ticket(ticket_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH a ticket. Callers pass only the Zoho fields they want changed
    (status, priority, assigneeId, departmentId, category, classification...)."""
    allowed = {
        "status",
        "priority",
        "assigneeId",
        "departmentId",
        "category",
        "subCategory",
        "classification",
    }
    payload = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not payload:
        raise ZohoDeskError("No updatable ticket fields supplied.", status=400)
    return await _request("PATCH", f"/api/v1/tickets/{ticket_id}", json_body=payload)


async def add_tags(ticket_id: str, names: List[str]) -> Any:
    """Associate tags (by name) with a ticket."""
    body = {"tags": [{"name": n} for n in names if n]}
    return await _request("POST", f"/api/v1/tickets/{ticket_id}/associateTag", json_body=body)


async def remove_tags(ticket_id: str, names: List[str]) -> Any:
    """Disassociate tags (by name) from a ticket."""
    body = {"tags": [{"name": n} for n in names if n]}
    return await _request("POST", f"/api/v1/tickets/{ticket_id}/disassociateTag", json_body=body)


async def list_agents(limit: int = 100) -> Dict[str, Any]:
    data = await _request("GET", "/api/v1/agents", params={"limit": min(max(1, limit), 200)})
    return data or {"data": []}


async def list_departments() -> Dict[str, Any]:
    data = await _request("GET", "/api/v1/departments", params={"isEnabled": "true"})
    return data or {"data": []}


async def ticket_count(*, department_id: Optional[str] = None) -> int:
    """Total ticket count via Zoho's /ticketsCount.

    Note: /ticketsCount filters by department/assignee/contact/date — NOT by
    `status` (status is not a supported filter there). Per-status breakdowns
    are derived from a recent-ticket sample in the route layer instead.
    Requires the ``Desk.search.READ`` OAuth scope.
    """
    params: Dict[str, Any] = {}
    if department_id:
        params["departmentId"] = department_id
    data = await _request("GET", "/api/v1/ticketsCount", params=params)
    try:
        return int((data or {}).get("count", 0))
    except (TypeError, ValueError):
        return 0
