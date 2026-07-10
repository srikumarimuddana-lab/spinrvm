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


async def require_section_head(
    company_id: str = Path(..., description="Corporate account ID"),
    section_id: str = Path(..., description="Section ID"),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Company admin OR the section's designated head (Q11: headship is the
    section's head_member_id attribute — a single member of any role — not a
    role value). Section-wallet management (Stripe top-ups, ledger reads) and
    own-section allowance writes hang off this guard.

    Returns the member ctx plus the section row and an ``is_head`` flag so
    handlers can distinguish "admin acting on any section" from "head acting
    on their own".
    """
    try:
        from ..db_supabase import get_rows  # type: ignore
    except ImportError:
        from db_supabase import get_rows  # type: ignore

    memberships = await list_active_memberships_for_user(current_user["id"])
    membership = next((m for m in memberships if m.get("company_id") == company_id), None)
    if not membership:
        raise HTTPException(status_code=403, detail="not a company member")

    sections = await get_rows("corporate_sections", {"id": section_id, "company_id": company_id}, limit=1)
    if not sections:
        # Cross-company probes read as absence, not permission (matches the
        # section-CRUD endpoints' 404-on-foreign-tenant behavior).
        raise HTTPException(status_code=404, detail="Section not found")
    section = sections[0]

    is_admin = membership.get("role") in _ADMIN_ROLES
    is_head = bool(section.get("head_member_id")) and section.get("head_member_id") == membership.get("id")
    if not (is_admin or is_head):
        raise HTTPException(status_code=403, detail="not this section's head")

    return {
        "user": current_user,
        "company_id": company_id,
        "role": membership["role"],
        "member_id": membership.get("id"),
        "member": membership,
        "section": section,
        "is_head": is_head,
    }
