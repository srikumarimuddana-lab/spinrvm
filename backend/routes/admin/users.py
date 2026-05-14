import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
except ImportError:
    import db_supabase
    from dependencies import get_admin_user

logger = logging.getLogger(__name__)

router = APIRouter()


class UserStatusRequest(BaseModel):
    status: Literal["active", "suspended", "banned"]


# ---------- Users (riders) ----------


@router.get("/users")
async def admin_get_users(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    role: Optional[str] = None,
):
    """Get users with optional role filter, search, and pagination.

    `role` accepts: "rider", "driver", "admin", or "all" (default).
    Legacy callers that omit `role` get every non-admin user — so
    anyone the admin sees in the app (rider or driver) is represented
    here. This replaces the old hardcoded `role='rider'` filter that
    silently hid driver-registered users from the Users page.
    """
    filters: Dict[str, Any] = {}
    role_param = (role or "all").lower()
    if role_param in ("rider", "driver", "admin"):
        filters["role"] = role_param
    elif role_param == "all":
        # Every non-admin. Admin dashboard users are managed on the Staff
        # page, not here.
        filters["role"] = {"$ne": "admin"}
    else:
        raise HTTPException(status_code=400, detail="role must be one of rider, driver, admin, all")

    if search:
        # Basic contains-match on phone / email / first_name; callers typically
        # pass partial phone digits or email substrings.
        term = search.strip()
        if term:
            filters["$or"] = [
                {"phone": {"$regex": re.escape(term), "$options": "i"}},
                {"email": {"$regex": re.escape(term), "$options": "i"}},
                {"first_name": {"$regex": re.escape(term), "$options": "i"}},
                {"last_name": {"$regex": re.escape(term), "$options": "i"}},
            ]
    users = await db_supabase.get_rows("users", filters, order="created_at", desc=True, limit=limit, offset=offset)
    return users


class UserSearchRequest(BaseModel):
    search: str
    role: Optional[str] = "all"
    limit: int = 5


@router.post("/users/search")
async def admin_search_users(
    body: UserSearchRequest,
    admin_user: dict = Depends(get_admin_user),
):
    """Typeahead search for users via POST body to keep search terms out of server logs."""
    return await admin_get_users(role=body.role, search=body.search, limit=body.limit)


@router.get("/users/{user_id}")
async def admin_get_user_details(user_id: str):
    """Get detailed user information."""
    user = await db_supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user's recent rides
    rides = await db_supabase.get_rows("rides", {"rider_id": user_id}, order="created_at", desc=True, limit=10)

    return {
        **user,
        "total_rides": await db_supabase.count_documents("rides", {"rider_id": user_id}),
        "recent_rides": rides,
    }


@router.put("/users/{user_id}/status")
async def admin_update_user_status(user_id: str, status_data: UserStatusRequest, admin: dict = Depends(get_admin_user)):
    """Update user status (e.g., suspend, activate)."""
    new_status = status_data.status

    user = await db_supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    old_status = user.get("status")

    await db_supabase.update_one(
        "users", {"id": user_id}, {"status": new_status, "updated_at": datetime.now(timezone.utc).isoformat()}
    )

    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "status_change",
            "entity_type": "user",
            "entity_id": user_id,
            "details": {"old_status": old_status, "new_status": new_status},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"message": f"User status updated to {new_status}"}


# ---------- DSAR (Data Subject Access Requests) ----------


@router.get("/dsars")
async def admin_list_dsars(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    admin: dict = Depends(get_admin_user),
):
    """DV-17: List PIPEDA data-export requests with SLA deadline tracking.

    PIPEDA s.9 requires a response within 30 days. Requests approaching or
    past their `response_due_at` should be prioritised.
    """
    filters: Dict[str, Any] = {}
    if status in ("pending", "in_progress", "completed", "rejected"):
        filters["status"] = status

    requests = await db_supabase.get_rows(
        "data_export_requests",
        filters,
        order="response_due_at",
        desc=False,
        limit=limit,
        offset=offset,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    for req in requests:
        due = req.get("response_due_at")
        if due and req.get("status") == "pending":
            req["overdue"] = due < now_iso

    return {"dsars": requests, "total": len(requests)}


@router.patch("/dsars/{request_id}/status")
async def admin_update_dsar_status(
    request_id: str,
    status: str,
    admin: dict = Depends(get_admin_user),
):
    """Update DSAR status (in_progress, completed, rejected)."""
    if status not in ("in_progress", "completed", "rejected"):
        raise HTTPException(status_code=400, detail="status must be one of in_progress, completed, rejected")

    req = await db_supabase.get_one("data_export_requests", {"id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="DSAR request not found")

    update: Dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if status == "completed":
        update["completed_at"] = datetime.now(timezone.utc).isoformat()

    await db_supabase.update_one("data_export_requests", {"id": request_id}, update)

    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": f"dsar_{status}",
            "entity_type": "data_export_request",
            "entity_id": request_id,
            "details": {"user_id": req.get("user_id"), "new_status": status},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"message": f"DSAR {request_id} marked {status}"}
