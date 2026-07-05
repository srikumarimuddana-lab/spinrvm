"""Resolve caller → company admin/member role. Mount as FastAPI dependency.

`/company/{company_id}/**` endpoints rely on one of these two guards to:
  1. Authenticate the caller (delegated to `get_current_user`).
  2. Confirm the caller is an *active* member of `company_id`.
  3. For write paths, confirm the role is owner/admin.

Using a dependency (not inline middleware) means FastAPI's
`app.dependency_overrides` mechanism works for tests.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Path

try:
    from ..db_supabase import list_active_memberships_for_user  # type: ignore
    from . import get_current_user  # type: ignore
except ImportError:
    from db_supabase import list_active_memberships_for_user  # type: ignore
    from dependencies import get_current_user  # type: ignore


_ADMIN_ROLES = {"owner", "admin"}


async def require_company_admin(
    company_id: str = Path(..., description="Corporate account ID"),
    current_user: dict = Depends(get_current_user),
) -> dict:
    memberships = await list_active_memberships_for_user(current_user["id"])
    for m in memberships:
        if m.get("company_id") == company_id and m.get("role") in _ADMIN_ROLES:
            # member_id/member: booking endpoints attribute spend to the
            # caller's corporate_members row — returning it here saves every
            # handler a re-query of the same membership list.
            return {
                "user": current_user,
                "company_id": company_id,
                "role": m["role"],
                "member_id": m.get("id"),
                "member": m,
            }
    raise HTTPException(status_code=403, detail="not a company admin")


async def require_company_member(
    company_id: str = Path(..., description="Corporate account ID"),
    current_user: dict = Depends(get_current_user),
) -> dict:
    memberships = await list_active_memberships_for_user(current_user["id"])
    for m in memberships:
        if m.get("company_id") == company_id:
            return {
                "user": current_user,
                "company_id": company_id,
                "role": m["role"],
                "member_id": m.get("id"),
                "member": m,
            }
    raise HTTPException(status_code=403, detail="not a company member")
