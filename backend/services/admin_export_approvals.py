"""Shared dual-approval gate for large admin-panel PII exports (AI-3,
docs/threat-model/admin-panel.md; ACTION_ITEMS.md B10). Backed by
``admin_export_approval_requests`` (migration 268).

Flow a gated export route follows:
    1. Before generating the file, call ``find_approved_grant`` with the
       exact ``route_key``/``params`` the request would use. If it returns a
       row, call ``consume`` with that row's id and proceed -- the export
       runs.
    2. If it returns ``None``, call ``create_request`` and return a
       "pending approval" response instead of the file. The requester
       cannot approve their own request (enforced here AND by the
       migration's ``CHECK`` constraint -- belt-and-suspenders on the one
       property this whole feature exists for).
    3. A different admin reviews the approval queue (``list_pending``) and
       calls ``approve`` or ``deny``.

Single-use by design: ``consume`` stamps ``consumed_at`` and
``find_approved_grant`` only matches unconsumed rows, so an approval can't
be replayed for a second export.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from .. import db_supabase
except ImportError:
    import db_supabase  # type: ignore


class SelfApprovalError(Exception):
    """Raised when an admin tries to approve/deny their own request."""


class RequestNotFound(Exception):
    pass


class RequestAlreadyDecided(Exception):
    """Raised when approve/deny is called on a non-pending request."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_request(
    *,
    requested_by: str,
    route_key: str,
    params: Dict[str, Any],
    row_count: Optional[int] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a pending approval request. Returns the inserted row."""
    doc = {
        "requested_by": requested_by,
        "route_key": route_key,
        "params": params,
        "row_count": row_count,
        "reason": reason,
        "status": "pending",
    }
    return await db_supabase.insert_one("admin_export_approval_requests", doc)


async def find_approved_grant(*, requested_by: str, route_key: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the most recent approved, unconsumed, unexpired request
    matching this exact requester/route/params, or None.

    Params equality is checked in Python (not pushed into the DB filter)
    since JSONB deep-equality filters vary by Postgres/PostgREST version;
    the candidate set is already tiny (one requester x one route, almost
    always zero or one row) so this is not a scan-the-table concern.
    """
    candidates = await db_supabase.get_rows(
        "admin_export_approval_requests",
        {"requested_by": requested_by, "route_key": route_key, "status": "approved"},
        order="decided_at",
        desc=True,
        limit=20,
    )
    now = datetime.now(timezone.utc)
    for row in candidates or []:
        if row.get("consumed_at"):
            continue
        if row.get("params") != params:
            continue
        expires_at = row.get("expires_at")
        if expires_at and datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")) < now:
            continue
        return row
    return None


async def find_pending_request(*, requested_by: str, route_key: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return an existing pending request matching this exact requester/
    route/params, or None. A gated route should call this before
    create_request so that repeatedly hitting the same export while it's
    awaiting approval doesn't spawn a new pending row every time."""
    candidates = await db_supabase.get_rows(
        "admin_export_approval_requests",
        {"requested_by": requested_by, "route_key": route_key, "status": "pending"},
        order="created_at",
        desc=True,
        limit=20,
    )
    for row in candidates or []:
        if row.get("params") == params:
            return row
    return None


async def consume(request_id: str) -> Dict[str, Any]:
    """Mark an approved request as consumed -- the gated export actually
    ran. Idempotency is the caller's job (call this once, right before
    generating the file, not speculatively)."""
    return await db_supabase.update_one(
        "admin_export_approval_requests", {"id": request_id}, {"consumed_at": _now_iso()}
    )


async def list_pending(*, limit: int = 50) -> List[Dict[str, Any]]:
    return await db_supabase.get_rows(
        "admin_export_approval_requests",
        {"status": "pending"},
        order="created_at",
        desc=False,
        limit=limit,
    )


async def _decide(request_id: str, *, decided_by: str, new_status: str, decision_note: Optional[str]) -> Dict[str, Any]:
    existing = await db_supabase.find_one("admin_export_approval_requests", {"id": request_id})
    if not existing:
        raise RequestNotFound(request_id)
    if existing.get("status") != "pending":
        raise RequestAlreadyDecided(f"request {request_id} is already {existing.get('status')}")
    if existing.get("requested_by") == decided_by:
        raise SelfApprovalError("an admin cannot approve or deny their own export request")
    return await db_supabase.update_one(
        "admin_export_approval_requests",
        {"id": request_id},
        {
            "status": new_status,
            "decided_by": decided_by,
            "decision_note": decision_note,
            "decided_at": _now_iso(),
        },
    )


async def approve(request_id: str, *, decided_by: str, decision_note: Optional[str] = None) -> Dict[str, Any]:
    return await _decide(request_id, decided_by=decided_by, new_status="approved", decision_note=decision_note)


async def deny(request_id: str, *, decided_by: str, decision_note: Optional[str] = None) -> Dict[str, Any]:
    return await _decide(request_id, decided_by=decided_by, new_status="denied", decision_note=decision_note)
