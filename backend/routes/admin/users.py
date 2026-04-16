import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

try:
    from ... import db_supabase
except ImportError:
    import db_supabase

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------- Users (riders) ----------


@router.get("/users")
async def admin_get_users(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
):
    """Get all users (riders) with optional search and pagination."""
    filters: Dict[str, Any] = {"role": "rider"}
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
async def admin_update_user_status(user_id: str, status_data: Dict[str, Any]):
    """Update user status (e.g., suspend, activate)."""
    valid_status = ["active", "suspended", "banned"]
    new_status = status_data.get("status")

    if new_status not in valid_status:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_status}")

    await db_supabase.update_one(
        "users", {"id": user_id}, {"status": new_status, "updated_at": datetime.utcnow().isoformat()}
    )
    return {"message": f"User status updated to {new_status}"}
