import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

try:
    from ...db import db
    from ...db_supabase import supabase
except ImportError:
    from db import db
    from db_supabase import supabase

# User-supplied search strings reach PostgREST's `.ilike()` filter. The
# operator treats `%` and `_` as wildcards, so we strip them + any
# PostgREST control characters (commas, parens, backslashes) before
# substituting into the filter pattern. Match length is capped so a
# single abusive query can't trigger a full-table scan.
_SEARCH_SAFE = re.compile(r"[%_,()\\]")
_MAX_SEARCH_LEN = 64


def _sanitize_search(raw: str) -> str:
    trimmed = raw.strip()[:_MAX_SEARCH_LEN]
    return _SEARCH_SAFE.sub("", trimmed)


logger = logging.getLogger(__name__)

router = APIRouter()

# ---------- Users (riders) ----------


@router.get("/users")
async def admin_get_users(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
):
    """Get all users (riders) with optional search and pagination.

    The old implementation passed the raw `search` string into a
    MongoDB-style ``{"$or": [{"first_name": {"$regex": search, ...}}, ...]}``
    filter. Two problems: (a) `db_supabase._apply_filters` does not
    understand ``$or`` or ``$regex``, so the search silently returned
    the unfiltered user list; and (b) even if the translator were
    fixed, piping unescaped user input into a regex is a ReDoS / query
    manipulation risk.

    The fix bypasses the generic filter abstraction and calls the
    Supabase client directly with an ``.or_()`` PostgREST filter
    composed of ``ilike`` clauses. User input is sanitized by
    :func:`_sanitize_search` first.
    """
    if not supabase:
        return []

    def _fn():
        q = supabase.table("users").select("*")
        if search:
            clean = _sanitize_search(search)
            if clean:
                # PostgREST .or_() takes a comma-separated filter list.
                # `ilike.*foo*` is case-insensitive substring match.
                q = q.or_(
                    f"first_name.ilike.%{clean}%,last_name.ilike.%{clean}%,email.ilike.%{clean}%,phone.ilike.%{clean}%"
                )
        q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
        res = q.execute()
        return res.data if res.data else []

    # get_rows is async; the filter path here is sync because we need
    # to chain Supabase query-builder calls that aren't awaitable.
    # Re-use the shared run_sync helper to dispatch it on a thread.
    try:
        from ...db_supabase import run_sync
    except ImportError:
        from db_supabase import run_sync
    return await run_sync(_fn)


@router.get("/users/{user_id}")
async def admin_get_user_details(user_id: str):
    """Get detailed user information."""
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user's recent rides
    rides = await db.get_rows("rides", {"rider_id": user_id}, order="created_at", desc=True, limit=10)

    return {
        **user,
        "total_rides": await db.rides.count_documents({"rider_id": user_id}),
        "recent_rides": rides,
    }


@router.put("/users/{user_id}/status")
async def admin_update_user_status(user_id: str, status_data: Dict[str, Any]):
    """Update user status (e.g., suspend, activate)."""
    valid_status = ["active", "suspended", "banned"]
    new_status = status_data.get("status")

    if new_status not in valid_status:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_status}")

    await db.users.update_one(
        {"id": user_id},
        {"$set": {"status": new_status, "updated_at": datetime.utcnow().isoformat()}},
    )
    return {"message": f"User status updated to {new_status}"}
